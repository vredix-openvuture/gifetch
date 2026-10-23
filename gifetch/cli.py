"""Command line: choose GIF and config (directly or from a preset), then draw them.
Options gifetch does not know go to fastfetch."""
import argparse
import os
import subprocess
import sys

from . import fastfetch, presets, render, sources
from .errors import GifetchError
from .sources import CONFIG_SUFFIXES, IMAGE_SUFFIXES, Source

USAGE = """\
gifetch [-g IMAGE | -rg FOLDER] [-c CONFIG | -rc FOLDER] [fastfetch options]
       gifetch -p NAME [fastfetch options]
       gifetch --new -p NAME [-g IMAGE | -rg FOLDER] [-c CONFIG | -rc FOLDER]
       gifetch -l | -d NAME"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gifetch",
        usage=USAGE,
        allow_abbrev=False,  # otherwise e.g. --ne would be taken as a prefix of --new
        add_help=False,  # only -h is ours; --help goes to fastfetch
        description="Animated GIF next to fastfetch in kitty. Without -g/-c the bundled default GIF and "
                    "config are used. Any option not listed here is passed to fastfetch, "
                    "e.g. gifetch --structure OS:Kernel.",
    )
    parser.add_argument("-h", action="help", help="show this help (fastfetch's own help: --help)")
    gif = parser.add_mutually_exclusive_group()
    gif.add_argument("-g", metavar="IMAGE", dest="gif", help="GIF (or PNG, JPEG, WebP) to show")
    gif.add_argument("-rg", metavar="FOLDER", dest="gif_folder", help="folder of images, shuffled: the next one each start")
    config = parser.add_mutually_exclusive_group()
    config.add_argument("-c", metavar="CONFIG", dest="config", help="fastfetch config to use")
    config.add_argument("-rc", metavar="FOLDER", dest="config_folder",
                        help="folder of fastfetch configs, shuffled the same way")
    parser.add_argument("-p", metavar="NAME", dest="preset", help="start a saved preset (with --new: its name)")
    parser.add_argument("--new", action="store_true", help="save -g/-rg and -c/-rc as preset -p NAME")
    parser.add_argument("-l", "--list", action="store_true", dest="list", help="list saved presets")
    parser.add_argument("-d", "--delete", metavar="NAME", dest="delete", help="delete a saved preset")
    return parser


def parse(argv):
    """Return (gifetch options, leftover arguments for fastfetch)."""
    parser = build_parser()
    args, fastfetch_args = parser.parse_known_args(argv)
    # `is not None` throughout, so an empty argument like -p "" is an error instead of being ignored.
    for flag, value in (("-g", args.gif), ("-c", args.config)):
        # -gr / -cr is a common slip for -rg / -rc; argparse reads it as -g "r".
        if value is not None and value.startswith("r") and f"{flag}{value}" in argv:
            parser.error(f"'{flag}{value}' means {flag} with the file '{value}'; for a random folder write -r{flag[1]} FOLDER")
    for arg in fastfetch_args:
        if arg.lower().partition("=")[0] == "--config":
            parser.error("use -c CONFIG (or -rc FOLDER) instead of --config")
        if arg.startswith(("-rg", "-rc")):
            parser.error(f"write {arg[:3]} FOLDER with a space, not '{arg}'")
    picks_sources = any(x is not None for x in (args.gif, args.gif_folder, args.config, args.config_folder))
    if args.list or args.delete is not None:
        if args.list and args.delete is not None or picks_sources or args.preset is not None or args.new:
            parser.error("-l and -d stand alone")
        if fastfetch_args:
            parser.error(f"unexpected arguments with -l/-d: {' '.join(fastfetch_args)}")
    if args.new:
        if args.preset is None:
            parser.error("--new needs a name: gifetch --new -p NAME [-g IMAGE | -rg FOLDER] [-c CONFIG | -rc FOLDER]")
        if fastfetch_args:
            parser.error(f"--new only saves; unexpected arguments: {' '.join(fastfetch_args)}")
    elif args.preset is not None and picks_sources:
        parser.error("-p starts a preset as saved; to save a new one add --new")
    return args, fastfetch_args


def chosen_sources(args):
    """(gif source, config source) from -g/-rg and -c/-rc, defaults where missing."""
    if args.gif is not None:
        gif = Source("file", args.gif)
    elif args.gif_folder is not None:
        gif = Source("folder", args.gif_folder)
    else:
        gif = sources.default_gif()
    if args.config is not None:
        config = Source("file", args.config)
    elif args.config_folder is not None:
        config = Source("folder", args.config_folder)
    else:
        config = sources.default_config()
    return gif, config


def describe(source):
    if source.is_folder:
        return f"random from {source.path}"
    if source in (sources.default_gif(), sources.default_config()):
        return f"bundled default ({os.path.basename(source.path)})"
    return os.path.basename(source.path)


def list_presets():
    saved = presets.names()
    if not saved:
        print(f"No presets yet. Save one with: gifetch --new -p NAME -g IMAGE -c CONFIG "
              f"(stored in {presets.config_dir()})")
    for name in saved:
        try:
            gif, config = presets.load(name)
        except GifetchError as e:  # one damaged preset must not hide the others
            print(f"{name}\n  damaged: {e}")
            continue
        print(f"{name}\n  gif:    {describe(gif)}\n  config: {describe(config)}")


def save_preset(name, gif, config):
    sources.check_image(gif)
    warnings = sources.check_config(config)
    presets.save(name, gif, config)
    for warning in warnings:
        print(f"gifetch: warning: {warning}", file=sys.stderr)
    print(f"Saved preset '{name}'. Start it with: gifetch -p {name}")


def can_draw_images():
    # The image protocol needs kitty on the other end; a pipe or another terminal gets plain fastfetch.
    # TERM, not KITTY_WINDOW_ID: the latter stays set inside tmux/screen, where placeholders fail.
    return os.environ.get("TERM") == "xterm-kitty" and sys.stdout.isatty()


def run(argv):
    args, fastfetch_args = parse(argv)
    if args.list:
        list_presets()
        return
    if args.delete is not None:
        presets.delete(args.delete)
        print(f"Deleted preset '{args.delete}'.")
        return
    if args.new:
        save_preset(args.preset, *chosen_sources(args))
        return

    gif, config = presets.load(args.preset) if args.preset is not None else chosen_sources(args)
    config_path = sources.pick(config, CONFIG_SUFFIXES)
    if os.path.isdir(config_path):
        raise GifetchError(f"'{config_path}' is a folder; for a random pick from it use -rc {config_path}")
    if not os.path.isfile(config_path):  # -c takes a file path, same as --new
        raise GifetchError(f"cannot read '{config_path}': no such file")
    if fastfetch.is_standalone(fastfetch_args) or not can_draw_images():
        fastfetch.replace_process(config_path, fastfetch_args)
        return
    gif_path = sources.pick(gif, IMAGE_SUFFIXES)
    sources.check_image_file(gif_path)
    render.show(gif_path, config_path, fastfetch_args)


def main(argv=None):
    try:
        run(sys.argv[1:] if argv is None else argv)
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader went away (e.g. `gifetch -l | head`); silence the flush at interpreter exit.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except GifetchError as e:
        sys.exit(f"gifetch: {e}")
    except subprocess.CalledProcessError as e:
        sys.exit(f"gifetch: {e.cmd[0]} failed (exit {e.returncode}){': ' + e.stderr.strip() if e.stderr else ''}")
    except FileNotFoundError as e:
        if e.filename in ("kitten", "fastfetch"):
            sys.exit(f"gifetch: '{e.filename}' not found - are kitty and fastfetch installed?")
        sys.exit(f"gifetch: {e.filename}: {e.strerror}")
    except OSError as e:
        sys.exit(f"gifetch: {e.filename or 'output'}: {e.strerror}")
