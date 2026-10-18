from unittest.mock import Mock
import pytest
from mx4_linux import actions
from mx4_linux.action_catalog import catalog_action


def adjustable():
    config = actions.default_config()
    config['actions']['zoom'] = catalog_action('adjustable-zoom')
    return config


def test_thumb_assignment_inherits_root_explicit_native_and_adapt_scope():
    config = adjustable()
    config['thumb_wheel'] = {'action': 'zoom', 'sensitivity': 1.0}
    config['assignment_profiles'] = {
        'app': {'label': 'App', 'app_id': 'example', 'ring': config['ring'][:], 'button_bindings': {}, 'thumb_wheel': None},
        'general': {'label': 'General', 'app_id': '*', 'ring': config['ring'][:], 'button_bindings': {}},
    }
    config['active_profiles'] = {'example': 'app', '*': 'general'}
    actions.validate_config(config)
    assert actions.resolve_config(config, 'example')['thumb_wheel'] is None
    assert actions.resolve_config(config, 'elsewhere')['thumb_wheel'] == config['thumb_wheel']
    config['adapt_to_app'] = False
    assert actions.resolve_config(config, 'example')['thumb_wheel'] == config['thumb_wheel']
    assert actions.resolve_config(config, 'example', for_editing=True)['thumb_wheel'] is None
    actions.validate_config(actions.resolve_config(config, 'example'))


@pytest.mark.parametrize('value', [True, {}, {'action': 'copy', 'sensitivity': 1}, {'action': 'missing', 'sensitivity': 1},
    {'action': 'zoom', 'sensitivity': True}, {'action': 'zoom', 'sensitivity': 0},
    {'action': 'zoom', 'sensitivity': float('nan')}, {'action': 'zoom', 'sensitivity': 4.1},
    {'action': 'zoom', 'sensitivity': 1, 'speed': 5}])
def test_bad_thumb_assignments_rejected(value):
    config = adjustable()
    config['thumb_wheel'] = value
    with pytest.raises(RuntimeError): actions.validate_config(config)


@pytest.mark.parametrize('step', [
    {'type': 'pointer', 'operation': 'click', 'button': 'left', 'count': 2},
    {'type': 'pointer', 'operation': 'move', 'dx': -300, 'dy': 10},
    {'type': 'pointer', 'operation': 'scroll', 'horizontal': -2, 'vertical': 3},
])
def test_pointer_steps_are_finite_guarded_and_dry_run_pure(step, monkeypatch):
    config = actions.default_config()
    config['actions']['copy']['steps'] = [step]
    actions.validate_config(config)
    assert actions.steps_send_input([step])
    monkeypatch.setattr('subprocess.Popen', Mock(side_effect=AssertionError('dry run created child')))
    assert actions.DesktopActions(config, dry_run=True).execute('copy')['steps'] == [step]


@pytest.mark.parametrize('step', [
    {'type': 'pointer', 'operation': 'click', 'button': 'left', 'count': True},
    {'type': 'pointer', 'operation': 'click', 'button': 'side', 'count': 1},
    {'type': 'pointer', 'operation': 'move', 'dx': float('inf'), 'dy': 0},
    {'type': 'pointer', 'operation': 'move', 'dx': 4097, 'dy': 0},
    {'type': 'pointer', 'operation': 'move', 'dx': 0, 'dy': 0},
    {'type': 'pointer', 'operation': 'scroll', 'horizontal': 0, 'vertical': 21},
    {'type': 'pointer', 'operation': 'scroll', 'horizontal': 0, 'vertical': 0},
])
def test_invalid_pointer_step_never_accepted(step):
    config = actions.default_config()
    config['actions']['copy']['steps'] = [step]
    with pytest.raises(RuntimeError): actions.validate_config(config)


def test_pointer_runtime_uses_cancellable_helper(monkeypatch):
    config = actions.default_config()
    step = {'type': 'pointer', 'operation': 'click', 'button': 'right', 'count': 1}
    config['actions']['copy']['steps'] = [step]
    invoked = Mock()
    monkeypatch.setattr(actions, 'pointer_operation', invoked, raising=False)
    actions.DesktopActions(config).execute('copy')
    assert invoked.call_args.args[0] == step
    invoked.call_args.args[1]()


def test_pointer_catalog_is_operating_and_clear_about_current_cursor():
    action = catalog_action('pointer-left-click')
    assert action['steps'] == [{'type': 'pointer', 'operation': 'click', 'button': 'left', 'count': 1}]
    assert 'cursor' in action['label'].lower()
    assert 'adjustment' in catalog_action('adjustable-horizontal-scroll')


def test_pointer_helper_parser_is_display_free_and_strict(tmp_path):
    from pathlib import Path
    import subprocess
    source = Path(__file__).resolve().parents[1] / 'native/pointer'
    assert (source / 'command.h').is_file(), 'finite native pointer parser is missing'
    test = tmp_path / 'parser.c'
    test.write_text('''#include "command.h"
#include <assert.h>
int main(void) {
 struct command c;
 const char *click[] = {"click", "left", "2"};
 assert(parse_command(3, click, &c)); assert(c.count == 2 && c.button == 272);
 const char *bad[] = {"move", "nan", "0"}; assert(!parse_command(3,bad,&c));
 const char *overflow[] = {"move", "99999999999999999999999", "0"}; assert(!parse_command(3,overflow,&c));
 const char *edge[] = {"move", "-4096", "4096"}; assert(parse_command(3,edge,&c));
 const char *extra[] = {"move", "1", "2", "3"}; assert(!parse_command(4,extra,&c));
 const char *zero[] = {"scroll", "0", "0"}; assert(!parse_command(3,zero,&c));
 const char *wide[] = {"scroll", "0", "21"}; assert(!parse_command(3,wide,&c));
 const char *decimal[] = {"move", "1.0", "0"}; assert(!parse_command(3,decimal,&c));
 const char *plus[] = {"move", "+1", "0"}; assert(!parse_command(3,plus,&c));
 return 0;
}''')
    binary = tmp_path / 'parser'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-fsanitize=undefined',
                    '-I', str(source), str(test), '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, env={'PATH': '/usr/bin', 'WAYLAND_DISPLAY': 'no-display'}, capture_output=True)
