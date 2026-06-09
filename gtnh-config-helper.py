import argparse
import logging
import re
import shutil
import sys
import tomllib
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('gtnh-config-helper')

VALID_SIDES = {"client", "server", "both"}


def main():
    # ### START MAIN ### #
    cwd = Path.cwd()

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='GTNH Config Helper')
    parser.add_argument('-s', '--side', choices=['client', 'server'], type=str.lower,
                        help='Chose between client and server install', required=True)
    parser.add_argument('-i', '--instance-path', help='Path to instance or server to configure', required=True)
    parser.add_argument('-c', '--config-path', help='Path to config file if non default')
    parser.add_argument('-v', '--verbose', action='store_true', help='Debug log level on console')
    args = parser.parse_args()

    init_logger(logging.DEBUG if args.verbose else logging.INFO)

    # Read configuration file
    if args.config_path is not None:
        toml_file = Path(args.config_path)
    else:
        toml_file = Path(cwd, 'gtnh-config-helper.toml')
    if not toml_file.is_file():
        sys.exit(f'FATAL ERROR: Could not find config file at "{toml_file}".')
    with open(toml_file, 'rb') as f:
        config = tomllib.load(f)

    logger.info(args.side)
    logger.info(config)

    # Test instance_path
    instance_dir = Path(args.instance_path)
    if not instance_dir.is_dir():
        sys.exit(f'FATAL ERROR: Could not find instance directory at "{instance_dir}".')
    if not is_minecraft_install(instance_dir, args.side):
        sys.exit(f'FATAL ERROR: Could not confirm "{instance_dir}" is path to valid a minecraft installation.')

    # Make backup directory
    backup_dir = instance_dir / 'gtnh-config-helper' / 'backups' / datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Self-update config from remote if a newer version is available
    config = maybe_update_config(toml_file, config, backup_dir)

    # Layer a local, non-shared custom config on top, if present
    custom_file = toml_file.with_name(f'{toml_file.stem}-custom{toml_file.suffix}')
    if custom_file.is_file():
        try:
            with open(custom_file, 'rb') as f:
                custom_config = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError) as e:
            sys.exit(f'FATAL ERROR: Could not read custom config "{custom_file}": {e}')
        config = deep_merge(config, custom_config)
        logger.info(f'Merged custom config from "{custom_file.name}"')
    else:
        logger.debug(f'No custom config at "{custom_file}"')

    # Configuration Management
    for name, entry in config.get("Config", {}).get("text", {}).items():
        logger.debug(f'Processing "{name}"')
        side = entry.get("side", "both")
        if side not in VALID_SIDES:
            logger.error(f'Invalid side value "{side}" in entry "{name}", skipping')
            continue
        if side != "both" and side != args.side:
            logger.debug(f'Skipping "{name}" (side: {side}, current: {args.side})')
            continue
        replacements = entry.get("replacements", [])
        if not replacements:
            logger.warning(f'[{name}] No replacements defined, skipping')
            continue
        apply_replacements_in_file(
            name,
            instance_dir / entry["file_path"],
            replacements,
            backup_dir,
        )

    # Mod Management (add, disable or replace)
    for name, entry in config.get("Config", {}).get("mods", {}).items():
        logger.debug(f'Processing "{name}"')
        side = entry.get("side", "both")
        if side not in VALID_SIDES:
            logger.error(f'Invalid side value "{side}" in entry "{name}", skipping')
            continue
        if side != "both" and side != args.side:
            logger.debug(f'Skipping "{name}" (side: {side}, current: {args.side})')
            continue

        default_mod_dir = Path('.minecraft/mods') if args.side == 'client' else Path('mods')
        download_or_disable_mod(
            name,
            Path.joinpath(instance_dir, entry.get("mod_dir", default_mod_dir)),
            entry.get("download_url"),
            entry.get("disable")
        )

    # ### END MAIN ### #
    sys.exit(0)


# ## Util ## #

# Loosely check if this look like a valid GTNH client or server install
def is_minecraft_install(path: Path, side: str) -> bool:
    if side == 'client':
        checks = [
            path.joinpath('.minecraft').is_dir(),
            path.joinpath('.minecraft', 'mods').is_dir(),
            path.joinpath('.minecraft', 'config').is_dir(),
            path.joinpath('gtnh_icon.png').is_file(),
        ]
        return all(checks)
    elif side == 'server':
        checks = [
            path.joinpath('mods').is_dir(),
            path.joinpath('config').is_dir(),
            path.joinpath('minecraft_server.1.7.10.jar').is_file(),
            path.joinpath('server.properties').is_file(),
            path.joinpath('eula.txt').is_file(),
        ]
        return all(checks)
    else:
        return False


# Recursively merge overlay into base: nested tables merge, any other value (string, list,
# bool, int) in overlay replaces the one in base. Mutates and returns base.
def deep_merge(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# Check [Settings].update_url for a newer config_version and, if found, replace the local
# config file (backing up the old one) and return the refreshed config. On any failure the
# local config is left untouched and returned as-is.
def maybe_update_config(toml_file: Path, config: dict, backup_dir: Path) -> dict:
    settings = config.get("Settings", {})
    url = settings.get("update_url")
    if not url:
        return config

    local_ver = settings.get("config_version", 0)

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            remote_bytes = resp.read()
        remote_config = tomllib.loads(remote_bytes.decode('utf-8'))
    except Exception as e:
        logger.warning(f'Config update check failed, keeping local config: {e}')
        return config

    remote_ver = remote_config.get("Settings", {}).get("config_version")
    if remote_ver is None:
        logger.warning('Remote config has no config_version, keeping local config')
        return config

    if remote_ver <= local_ver:
        logger.debug(f'Config is up to date (local v{local_ver}, remote v{remote_ver})')
        return config

    # Newer version available: back up the current file, then overwrite it
    backup_dest = backup_dir / toml_file.name
    try:
        if not backup_dest.exists():
            shutil.copy2(toml_file, backup_dest)
            logger.debug(f'Backed up config to "{backup_dest}"')
        toml_file.write_bytes(remote_bytes)
    except OSError as e:
        logger.warning(f'Could not write updated config, keeping local config: {e}')
        return config

    logger.info(f'Updated config v{local_ver} -> v{remote_ver} from "{url}"')
    return remote_config


# Apply a list of find/replace operations to a single file (single read, single write, single backup)
def apply_replacements_in_file(name: str, file: Path, replacements: list, backup_dir: Path) -> bool:
    prefix = f'[{name}]'
    try:
        content = file.read_text(encoding='utf-8')
    except FileNotFoundError:
        logger.warning(f'{prefix} File not found: "{file}"')
        return False
    except OSError as e:
        logger.error(f'{prefix} Could not read "{file}": {e}')
        return False

    original_content = content
    for i, rep in enumerate(replacements):
        sub_prefix = f'{prefix}[{i}]'
        search = rep.get("finds_str")
        replacement = rep.get("new_str")
        use_regex = rep.get("use_regex", False)
        if search is None or replacement is None:
            logger.error(f'{sub_prefix} Missing "finds_str" or "new_str", skipping')
            continue

        if use_regex:
            if re.search(search, content) is None:
                if replacement in content:
                    logger.info(f'{sub_prefix} Already correctly set, skipping')
                else:
                    logger.warning(f'{sub_prefix} Pattern did not match in "{file}" — unexpected config state')
                continue
            content = re.sub(search, replacement, content)
        else:
            if search not in content:
                if replacement in content:
                    logger.info(f'{sub_prefix} Already correctly set, skipping')
                else:
                    logger.warning(f'{sub_prefix} String not found in "{file}" — unexpected config state')
                continue
            content = content.replace(search, replacement)
        logger.info(f'{sub_prefix} Replaced successfully')

    if content == original_content:
        logger.debug(f'{prefix} No changes to "{file.name}"')
        return True

    backup_dest = backup_dir / file.name
    if not backup_dest.exists():
        shutil.copy2(file, backup_dest)
        logger.debug(f'{prefix} Backed up "{file.name}"')
    else:
        logger.debug(f'{prefix} Backup already exists for "{file.name}", skipping')

    try:
        file.write_text(content, encoding='utf-8')
    except OSError as e:
        logger.error(f'{prefix} Could not write "{file}": {e}')
        return False
    return True


def download_or_disable_mod(name: str, full_mod_dir: Path, download_url: str | None,
                            disable_pattern: str | None) -> bool:
    prefix = f'[{name}]'

    if not full_mod_dir.is_dir():
        logger.error(f'{prefix} Mod directory not found: "{full_mod_dir}"')
        return False

    # If we know the target filename, check if it's already there
    if download_url:
        filename = Path(download_url).name
        dest = full_mod_dir / filename
        if dest.is_file():
            logger.info(f'{prefix} Already present, skipping: "{filename}"')
            return True

    # Disable/backup matching jars
    if disable_pattern:
        try:
            matched = [f for f in full_mod_dir.iterdir()
                       if f.suffix == '.jar' and re.search(disable_pattern, f.name)]
            if not matched:
                logger.warning(f'{prefix} No jars matched disable pattern: {disable_pattern}')
            for jar in matched:
                bak = jar.with_suffix('.jar.bak')
                jar.rename(bak)
                logger.info(f'{prefix} Disabled: "{jar.name}" → "{bak.name}"')
        except OSError as e:
            logger.error(f'{prefix} Error during disable: {e}')
            return False

    # Download mod
    if download_url:
        try:
            logger.info(f'{prefix} Downloading "{filename}"...')
            urllib.request.urlretrieve(download_url, dest)
            logger.info(f'{prefix} Downloaded successfully: "{filename}"')
        except Exception as e:
            logger.error(f'{prefix} Download failed: {e}')
            return False

    return True


# ## INIT ## #

# Init logger
def init_logger(console_log_level=logging.INFO):
    logger.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(console_log_level)
    console.setFormatter(logging.Formatter('%(asctime)s :: %(levelname)-8s :: %(message)s', datefmt='%y-%m-%d %H:%M'))
    logger.addHandler(console)


if __name__ == '__main__':
    main()
