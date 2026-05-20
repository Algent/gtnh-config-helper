import argparse
import logging
import re
import sys
import tomllib
from pathlib import Path

logger = logging.getLogger('gtnh-config-helper')

VALID_SIDES = {"client", "server", "both"}


def main():
    # ### START MAIN ### #
    cwd = Path.cwd()
    script_dir = Path(__file__).parent

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

    # TODO Don't forget to process path from arg
    # TODO loop on specific config keys and replace matching strings in files
    for name, entry in config["Config"]["text"].items():
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
            entry.get("use_regex", False)
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
def replace_string_in_file(name: str, file: Path, search: str, replacement: str, use_regex: bool = False) -> bool:
    prefix = f'[{name}]'
    try:
        content = file.read_text(encoding='utf-8')
    except FileNotFoundError:
        logger.warning(f'{prefix} File not found: "{file}"')
        return False
    except OSError as e:
        logger.error(f'{prefix} Could not read "{file}": {e}')
        return False

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
        logger.error(f'{prefix}Could not write "{file}": {e}')
        return False

    logger.info(f'{prefix} Replaced successfully')
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
