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
        replace_string_in_file(
            name,
            instance_dir / entry["file_path"],
            entry["finds_str"],
            entry["new_str"],
            backup_dir,
            entry.get("use_regex", False)
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


# Function to replace string inside file, taking path, search and replace
def replace_string_in_file(name: str, file: Path, search: str, replacement: str, backup_dir: Path,
                           use_regex: bool = False) -> bool:
    prefix = f'[{name}]'
    try:
        content = file.read_text(encoding='utf-8')
    except FileNotFoundError:
        logger.warning(f'{prefix} File not found: "{file}"')
        return False
    except OSError as e:
        logger.error(f'{prefix} Could not read "{file}": {e}')
        return False

    backup_dest = backup_dir / file.name
    if not backup_dest.exists():
        shutil.copy2(file, backup_dest)
        logger.debug(f'{prefix} Backed up "{file.name}"')
    else:
        logger.debug(f'{prefix} Backup already exists for "{file.name}", skipping')

    if use_regex:
        if re.search(search, content) is None:
            logger.warning(f'{prefix} Pattern did not match in "{file}" — unexpected config state')
            return False
        if replacement in content:  # already set correctly
            logger.info(f'{prefix} Already correctly set, skipping')
            return True
        new_content = re.sub(search, replacement, content)
    else:
        if search not in content:
            logger.warning(f'{prefix} String not found in "{file}" — unexpected config state')
            return False
        if replacement in content:
            logger.info(f'{prefix} Already correctly set, skipping')
            return True
        new_content = content.replace(search, replacement)

    try:
        file.write_text(new_content, encoding='utf-8')
    except OSError as e:
        logger.error(f'{prefix} Could not write "{file}": {e}')
        return False

    logger.info(f'{prefix} Replaced successfully')
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
