"""Font prerequisite installation is local, offline and validates real metadata."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
REGULAR = 'JetBrains Mono\t80\t0\tFalse\n'
BOLD = 'JetBrains Mono\t200\t0\tFalse\n'


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / 'package with spaces; literal $dollar'
    script = project / 'scripts' / 'setup-font.sh'
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / 'scripts' / 'setup-font.sh', script)
    fonts = project / 'src' / 'mx4_linux' / 'assets' / 'fonts'
    fonts.mkdir(parents=True)
    (fonts / 'JetBrainsMono-Regular.ttf').write_text(REGULAR)
    (fonts / 'JetBrainsMono-Bold.ttf').write_text(BOLD)
    (fonts / 'OFL.txt').write_text('Test license fixture\n')
    commands = tmp_path / 'commands'
    commands.mkdir()
    program = f'''#!{sys.executable}
import json, os
from pathlib import Path
import sys
command=Path(sys.argv[0]).name
with open(os.environ['TEST_FONT_LOG'], 'a') as log:
    log.write(json.dumps([command, *sys.argv[1:]])+'\\n')
if command == 'fc-list':
    print(os.environ.get('TEST_INSTALLED_FONTS',''), end='')
    for path in Path(os.environ['XDG_DATA_HOME']).glob('fonts/mx4-linux-jetbrains-mono/*.ttf'):
        print(path.read_text(), end='')
elif command == 'fc-scan':
    print(Path(sys.argv[-1]).read_text(), end='')
'''
    for name in ('fc-list', 'fc-scan', 'fc-cache'):
        path = commands / name
        path.write_text(program)
        path.chmod(0o755)
    env = {**os.environ, 'PATH': f'{commands}:{os.environ["PATH"]}',
           'XDG_DATA_HOME': str(tmp_path / 'user data'), 'TEST_FONT_LOG': str(tmp_path / 'commands.jsonl')}
    destination = Path(env['XDG_DATA_HOME']) / 'fonts' / 'mx4-linux-jetbrains-mono'
    return script, fonts, destination, env


def invoke(setup, *args):
    script, _fonts, _destination, env = setup
    return subprocess.run([str(script), *args], env=env, text=True, capture_output=True)


def test_missing_font_check_has_no_installation_side_effects(setup):
    result = invoke(setup, '--check')
    assert result.returncode == 1
    assert 'No fonts were installed' in result.stderr
    assert not setup[2].exists()


def test_default_setup_installs_bundled_fonts_license_and_is_idempotent(setup):
    result = invoke(setup)
    assert result.returncode == 0, result.stderr
    assert len(list(setup[2].glob('*.ttf'))) == 2
    assert (setup[2] / 'OFL.txt').read_text() == 'Test license fixture\n'
    assert invoke(setup, '--check').returncode == 0
    assert invoke(setup).returncode == 0
    calls = [json.loads(line) for line in Path(setup[3]['TEST_FONT_LOG']).read_text().splitlines()]
    assert [call for call in calls if call[0] == 'fc-cache'] == [['fc-cache', '--force', str(setup[2].parent)]]
    assert all(path.stat().st_mode & 0o777 == 0o644 for path in setup[2].iterdir())


@pytest.mark.parametrize('metadata', ['Wrong Font\t80\t0\tFalse\n',
                                     'JetBrains Mono Clone\t80\t0\tFalse\n',
                                     'JetBrains Mono\t80\t100\tFalse\n', 'invalid font'])
def test_wrong_or_incomplete_bundle_is_rejected_before_installing(setup, metadata):
    (setup[1] / 'JetBrainsMono-Regular.ttf').write_text(metadata)
    assert invoke(setup).returncode == 1
    assert not setup[2].exists()


def test_missing_bundled_license_is_rejected_before_installing(setup):
    (setup[1] / 'OFL.txt').unlink()
    assert invoke(setup).returncode == 1
    assert not setup[2].exists()


def test_preinstalled_family_satisfies_requirement_without_copying_fonts(setup):
    setup[3]['TEST_INSTALLED_FONTS'] = REGULAR + BOLD
    assert invoke(setup).returncode == 0
    assert not setup[2].exists()


def test_installer_does_not_follow_destination_symlink_or_modify_other_families(setup, tmp_path):
    unrelated = tmp_path / 'unrelated fonts'
    unrelated.mkdir()
    sentinel = unrelated / 'Other.ttf'
    sentinel.write_text('keep')
    setup[2].parent.mkdir(parents=True)
    setup[2].symlink_to(unrelated, target_is_directory=True)
    result = invoke(setup)
    assert result.returncode == 1
    assert sentinel.read_text() == 'keep'
    assert list(unrelated.iterdir()) == [sentinel]


def test_bundled_font_files_have_expected_metadata_and_license():
    if not shutil.which('fc-scan'):
        pytest.skip('Fontconfig required for actual font inspection')
    fonts = ROOT / 'src' / 'mx4_linux' / 'assets' / 'fonts'
    for name, weight in [('Regular', '80'), ('Bold', '200')]:
        result = subprocess.run(['fc-scan', '--format', '%{family}\t%{weight}\t%{slant}',
                                 str(fonts / f'JetBrainsMono-{name}.ttf')], text=True, capture_output=True, check=True)
        assert result.stdout == f'JetBrains Mono\t{weight}\t0'
    assert 'SIL OPEN FONT LICENSE Version 1.1' in (fonts / 'OFL.txt').read_text()


def test_source_setup_stops_before_python_when_font_prerequisite_fails(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copy2(ROOT / 'scripts' / 'setup.sh', scripts / 'setup.sh')
    helper = scripts / 'setup-font.sh'
    helper.write_text('#!/bin/sh\nexit 3\n')
    helper.chmod(0o755)
    python = tmp_path / 'python probe'
    python.write_text('#!/bin/sh\nprintf "unexpected Python invocation" >&2\nexit 99\n')
    python.chmod(0o755)
    result = subprocess.run([str(scripts / 'setup.sh')], env={**os.environ, 'PYTHON': str(python)},
                            text=True, capture_output=True)
    assert result.returncode == 3
    assert 'unexpected' not in result.stderr
    assert not (tmp_path / '.venv').exists()


def test_actual_fontconfig_can_install_and_discover_bundled_fonts_in_isolation(tmp_path):
    if not all(shutil.which(command) for command in ('fc-list', 'fc-scan', 'fc-cache')):
        pytest.skip('Fontconfig commands required for isolated installation')
    data = tmp_path / 'data'
    config = tmp_path / 'fonts.conf'
    config.write_text('<fontconfig><reset-dirs/><dir prefix="xdg">fonts</dir>'
                      '<cachedir prefix="xdg">fontconfig</cachedir>'
                      '<selectfont><rejectfont><glob>*</glob></rejectfont>'
                      f'<acceptfont><glob>{data}/*</glob></acceptfont></selectfont></fontconfig>')
    (data / 'fonts').mkdir(parents=True)
    # Deterministically reproduce a parent cache whose timestamp cannot reveal
    # a newly created child (for example two writes in one coarse timestamp).
    # Font scanning, cache generation and discovery still use real Fontconfig.
    font_root = data / 'fonts'
    timestamp = 1_700_000_000
    os.utime(font_root, (timestamp, timestamp))
    real_cache = shutil.which('fc-cache')
    commands = tmp_path / 'real-fontconfig-wrapper'
    commands.mkdir()
    cache = commands / 'fc-cache'
    cache.write_text(f'#!{sys.executable}\nimport os, sys\n'
                     f'os.utime({str(font_root)!r}, ({timestamp}, {timestamp}))\n'
                     f'os.execv({real_cache!r}, [{real_cache!r}, *sys.argv[1:]])\n')
    cache.chmod(0o755)
    env = {**os.environ, 'FONTCONFIG_FILE': str(config), 'FONTCONFIG_PATH': str(tmp_path),
           'XDG_DATA_HOME': str(data), 'XDG_CACHE_HOME': str(tmp_path / 'cache'),
           'PATH': f'{commands}:{os.environ["PATH"]}'}
    script = ROOT / 'scripts' / 'setup-font.sh'
    check = subprocess.run([str(script), '--check'], env=env, text=True, capture_output=True)
    assert check.returncode == 1
    install = subprocess.run([str(script)], env=env, text=True, capture_output=True)
    assert install.returncode == 0, install.stderr
    check = subprocess.run([str(script), '--check'], env=env, text=True, capture_output=True)
    assert check.returncode == 0, check.stderr
    assert len(list((data / 'fonts' / 'mx4-linux-jetbrains-mono').glob('*.ttf'))) == 2


def test_font_setup_honors_python_override_with_spaces(setup, tmp_path):
    marker = tmp_path / 'python-called'
    python = tmp_path / 'selected python'
    python.write_text(f'#!{sys.executable}\n'
                      'import os, sys\n'
                      f'from pathlib import Path\nPath({str(marker)!r}).write_text("called")\n'
                      f'os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n')
    python.chmod(0o755)
    setup[3]['PYTHON'] = str(python)
    setup[3]['TEST_INSTALLED_FONTS'] = REGULAR + BOLD
    result = invoke(setup, '--check')
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == 'called'
