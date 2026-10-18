# Select only the hidraw interfaces covered by 70-mx4-linux.rules.
mx4_hidraw_paths() {
    local hidraw_root="${1:-/sys/class/hidraw}"
    local hidraw_path property value
    for hidraw_path in "$hidraw_root"/hidraw*; do
        [[ -r "$hidraw_path/device/uevent" ]] || continue
        while IFS='=' read -r property value; do
            [[ "$property" == HID_ID ]] || continue
            case "$value" in
                0003:0000046D:0000C548|0005:0000046D:0000B042|0005:0000046D:0000B048)
                    printf '%s\0' "$hidraw_path"
                    ;;
            esac
            break
        done < "$hidraw_path/device/uevent"
    done
}
