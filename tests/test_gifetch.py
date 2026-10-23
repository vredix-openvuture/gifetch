"""CLI, sources and preset store, against a throwaway XDG_CONFIG_HOME. Drawing and exec are stubbed
out; config checks run the real fastfetch."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import base64
import json
import struct
from concurrent.futures import ThreadPoolExecutor

from gifetch import cli, images, kitty, presets, render, shuffle, sources
from gifetch.errors import GifetchError

TESTCARD = os.path.join(sources.ROOT, "assets", "testcard.gif")
FIXTURES = os.path.join(sources.ROOT, "tests", "fixtures")
SHORT_CONFIG = '// comment\n{ "modules": ["os", "kernel",], }\n'
NERD_FONT_KEY = "\\uf31a"  # JSON escape, so the source file stays free of invisible characters


class GifetchTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = home.name
        self.patch(mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(self.home, "cfg"),
                                                "XDG_CACHE_HOME": os.path.join(self.home, "cache")}))
        self.show = self.patch(mock.patch.object(cli.render, "show"))
        self.replace_process = self.patch(mock.patch.object(cli.fastfetch, "replace_process"))
        self.patch(mock.patch.object(cli, "can_draw_images", return_value=True))
        self.stdout = self.patch(mock.patch("sys.stdout"))
        self.stderr = self.patch(mock.patch("sys.stderr"))

    def patch(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def write(self, name, content):
        path = os.path.join(self.home, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def gif_folder(self):
        folder = os.path.join(self.home, "gifs")
        os.makedirs(folder)
        shutil.copyfile(sources.DEFAULT_GIF, os.path.join(folder, "a.gif"))
        shutil.copyfile(TESTCARD, os.path.join(folder, "b.GIF"))
        self.write("gifs/notes.txt", "ignored")
        return folder

    def printed(self, stream):
        return "".join(c.args[0] for c in stream.write.call_args_list)

    # Starting without a preset

    def test_no_options_use_bundled_defaults(self):
        cli.run([])
        self.show.assert_called_once_with(sources.DEFAULT_GIF, sources.DEFAULT_CONFIG, [])

    def test_g_and_c_are_used_directly_and_nothing_is_saved(self):
        config = self.write("mine.jsonc", SHORT_CONFIG)
        cli.run(["-g", TESTCARD, "-c", config])
        self.show.assert_called_once_with(TESTCARD, config, [])
        self.assertEqual(presets.names(), [])

    def test_g_alone_keeps_default_config(self):
        cli.run(["-g", TESTCARD])
        self.show.assert_called_once_with(TESTCARD, sources.DEFAULT_CONFIG, [])

    def test_rg_picks_from_folder_case_insensitive_and_ignores_other_files(self):
        folder = self.gif_folder()
        seen = set()
        for _ in range(40):
            cli.run(["-rg", folder])
            seen.add(os.path.basename(self.show.call_args.args[0]))
        self.assertEqual(seen, {"a.gif", "b.GIF"})

    def test_rg_runs_through_every_gif_before_repeating_and_never_twice_in_a_row(self):
        folder = os.path.join(self.home, "many")
        os.makedirs(folder)
        for i in range(5):
            shutil.copyfile(TESTCARD, os.path.join(folder, f"{i}.gif"))
        picks = []
        for _ in range(50):
            cli.run(["-rg", folder])
            picks.append(os.path.basename(self.show.call_args.args[0]))
        for start in range(0, 50, 5):
            self.assertEqual(len(set(picks[start:start + 5])), 5, picks)
        self.assertTrue(all(a != b for a, b in zip(picks, picks[1:])), picks)
        self.assertGreater(len({tuple(picks[i:i + 5]) for i in range(0, 50, 5)}), 1, "every round in the same order")

    def test_rg_and_rc_on_the_same_folder_keep_separate_orders(self):
        folder = os.path.join(self.home, "both")
        os.makedirs(folder)
        for i in range(3):
            shutil.copyfile(TESTCARD, os.path.join(folder, f"{i}.gif"))
            self.write(f"both/{i}.jsonc", SHORT_CONFIG)
        gifs = []
        for _ in range(9):
            cli.run(["-rg", folder, "-rc", folder])
            gifs.append(os.path.basename(self.show.call_args.args[0]))
        for start in range(0, 9, 3):
            self.assertEqual(len(set(gifs[start:start + 3])), 3, gifs)

    def test_damaged_shuffle_state_starts_a_fresh_round(self):
        folder = self.gif_folder()
        cli.run(["-rg", folder])
        state = shuffle._state_file(folder, sources.IMAGE_SUFFIXES)
        for content in ('{"queue": 5}', '{"queue": [1, 2], "last": 3}', "[]", "{broken"):
            with open(state, "w") as f:
                f.write(content)
            cli.run(["-rg", folder])
            with open(state) as f:
                self.assertIsInstance(json.load(f)["queue"], list)

    def test_parallel_starts_never_draw_the_same_gif_in_one_round(self):
        folder = os.path.join(self.home, "forty")
        os.makedirs(folder)
        files = [os.path.join(folder, f"f{i}.gif") for i in range(40)]
        for f in files:
            shutil.copyfile(TESTCARD, f)
        with ThreadPoolExecutor(max_workers=20) as pool:
            picks = list(pool.map(lambda _: shuffle.next_file(folder, files, (".gif",)), range(20)))
        self.assertEqual(len(set(picks)), 20, picks)

    def test_rc_picks_a_config_from_folder(self):
        config = self.write("configs/one.jsonc", SHORT_CONFIG)
        self.write("configs/readme.md", "ignored")
        cli.run(["-rc", os.path.dirname(config)])
        self.assertEqual(self.show.call_args.args[1], config)

    def test_empty_or_missing_folder_is_a_clean_error(self):
        os.makedirs(os.path.join(self.home, "empty"))
        with self.assertRaisesRegex(GifetchError, "no .gif/.png/.jpg/.jpeg/.webp files"):
            cli.run(["-rg", os.path.join(self.home, "empty")])
        with self.assertRaisesRegex(GifetchError, "cannot read folder"):
            cli.run(["-rc", "/does/not/exist"])

    def test_folder_given_to_g_or_c_points_to_rg_and_rc(self):
        folder = self.gif_folder()
        with self.assertRaisesRegex(GifetchError, "is a folder; .* use -rg"):
            cli.run(["-g", folder])
        with self.assertRaisesRegex(GifetchError, "is a folder; .* use -rc"):
            cli.run(["-c", folder])
        with self.assertRaisesRegex(GifetchError, "is a folder; .* use -rc"):
            cli.run(["--new", "-p", "x", "-c", folder])

    def test_swapped_gr_and_cr_are_explained(self):
        for flag in ("-gr", "-cr"):
            with self.assertRaises(SystemExit, msg=flag):
                cli.run([flag, "/some/folder"])
        self.assertIn("-rg FOLDER", self.printed(self.stderr))
        self.show.assert_not_called()

    def test_truncated_gif_is_a_clean_error(self):
        path = os.path.join(self.home, "short.gif")
        with open(path, "wb") as f:
            f.write(b"GIF89a\x01")
        with self.assertRaisesRegex(GifetchError, "no image size"):
            images.size(path)
        with open(path, "wb") as f:
            f.write(b"GIF89a\x00\x00\x10\x00")
        with self.assertRaisesRegex(GifetchError, "no image size"):
            images.size(path)

    def test_still_images_are_shown_and_preset_copies_keep_their_suffix(self):
        png = os.path.join(FIXTURES, "sample.png")
        cli.run(["-g", png])
        self.show.assert_called_once_with(png, sources.DEFAULT_CONFIG, [])
        cli.run(["--new", "-p", "still", "-g", png])
        cli.run(["-p", "still"])
        self.assertEqual(os.path.basename(self.show.call_args.args[0]), "image.png")

    def test_preset_copy_is_named_after_the_detected_format(self):
        disguised = os.path.join(self.home, "weird.txt")
        shutil.copyfile(os.path.join(FIXTURES, "lossy.webp"), disguised)
        cli.run(["--new", "-p", "odd", "-g", disguised])
        self.assertTrue(os.path.isfile(os.path.join(presets.preset_dir("odd"), "image.webp")))

    def test_damaged_image_is_refused_on_save(self):
        with open(os.path.join(FIXTURES, "sample.png"), "rb") as f:
            broken = f.read()[:20]
        path = os.path.join(self.home, "broken.png")
        with open(path, "wb") as f:
            f.write(broken)
        with self.assertRaisesRegex(GifetchError, "no image size"):
            cli.run(["--new", "-p", "broken", "-g", path])
        self.assertEqual(presets.names(), [])

    def test_rg_takes_every_supported_format(self):
        folder = os.path.join(self.home, "mixed")
        shutil.copytree(FIXTURES, folder)
        self.write("mixed/notes.txt", "ignored")
        seen = set()
        for _ in range(len(os.listdir(FIXTURES))):
            cli.run(["-rg", folder])
            seen.add(os.path.basename(self.show.call_args.args[0]))
        self.assertEqual(seen, set(os.listdir(FIXTURES)))

    def test_non_gif_is_refused_before_drawing(self):
        with self.assertRaisesRegex(GifetchError, "not a GIF, PNG, JPEG or WebP"):
            cli.run(["-g", __file__])
        self.show.assert_not_called()

    # Presets

    def test_new_copies_files_and_p_starts_them(self):
        config = self.write("mine.jsonc", SHORT_CONFIG)
        cli.run(["--new", "-p", "card", "-g", TESTCARD, "-c", config])
        self.show.assert_not_called()
        os.remove(config)  # the preset holds its own copy
        cli.run(["-p", "card"])
        gif_path, config_path, _ = self.show.call_args.args
        self.assertEqual(gif_path, os.path.join(presets.preset_dir("card"), "image.gif"))
        with open(config_path) as f:
            self.assertEqual(f.read(), SHORT_CONFIG)

    def test_new_without_g_and_c_follows_the_bundled_defaults(self):
        cli.run(["--new", "-p", "plain"])
        cli.run(["-p", "plain"])
        self.show.assert_called_once_with(sources.DEFAULT_GIF, sources.DEFAULT_CONFIG, [])
        self.assertEqual(os.listdir(presets.preset_dir("plain")), ["preset.json"])

    def test_new_with_folder_keeps_the_path_so_later_gifs_count(self):
        folder = self.gif_folder()
        cli.run(["--new", "-p", "mix", "-rg", folder])
        shutil.copyfile(TESTCARD, os.path.join(folder, "c.gif"))
        seen = set()
        for _ in range(60):
            cli.run(["-p", "mix"])
            seen.add(os.path.basename(self.show.call_args.args[0]))
        self.assertIn("c.gif", seen)

    def test_new_checks_every_config_in_a_folder(self):
        self.write("configs/good.jsonc", SHORT_CONFIG)
        self.write("configs/bad.jsonc", '{ "modules": [ "os" }')
        with self.assertRaisesRegex(GifetchError, "fastfetch rejects .*bad.jsonc"):
            cli.run(["--new", "-p", "x", "-rc", os.path.join(self.home, "configs")])
        self.assertEqual(presets.names(), [])

    def test_new_refuses_broken_config_and_missing_files(self):
        bad = self.write("bad.jsonc", '{ "modules": [ "os" }')
        with self.assertRaisesRegex(GifetchError, "fastfetch rejects"):
            cli.run(["--new", "-p", "x", "-c", bad])
        for argv in (["-c", "/does/not/exist.jsonc"], ["-c", ""], ["-g", "/does/not/exist.gif"]):
            with self.assertRaisesRegex(GifetchError, "cannot read", msg=argv):
                cli.run(["--new", "-p", "x", *argv])
        self.assertEqual(presets.names(), [])

    def test_new_prints_layout_warnings(self):
        config = self.write(
            "w.jsonc",
            '{ "display": { "key": { "width": 10 } }, "modules": [{"type": "os", "key": "' + NERD_FONT_KEY + '"}] }',
        )
        cli.run(["--new", "-p", "w", "-c", config])
        self.assertIn("display.key.width", self.printed(self.stderr))
        self.assertIn("Nerd Font", self.printed(self.stderr))

    def test_duplicate_and_bad_names_are_refused(self):
        cli.run(["--new", "-p", "card", "-g", TESTCARD])
        with self.assertRaisesRegex(GifetchError, "already exists"):
            cli.run(["--new", "-p", "card"])
        for name in ("../evil", "a/b", ".hidden", "-dash", "", "nl\n"):
            with self.assertRaises(GifetchError, msg=name):
                presets.save(name, sources.default_gif(), sources.default_config())

    def test_unknown_preset_names_the_saved_ones(self):
        cli.run(["--new", "-p", "card", "-g", TESTCARD])
        with self.assertRaisesRegex(GifetchError, "saved: card"):
            cli.run(["-p", "nope"])

    def test_damaged_preset_is_a_clean_error_and_list_goes_on(self):
        cli.run(["--new", "-p", "a"])
        cli.run(["--new", "-p", "b"])
        for content in ("[]", '{"gif": {"kind": "file", "path": 1}, "config": {"kind": "file", "path": "x"}}'):
            self.write("cfg/gifetch/presets/a/preset.json", content)
            with self.assertRaisesRegex(GifetchError, "damaged", msg=content):
                cli.run(["-p", "a"])
        cli.run(["-l"])
        self.assertIn("damaged", self.printed(self.stdout))
        self.assertIn("b\n  gif:    bundled default", self.printed(self.stdout))

    def test_leftover_folder_without_preset_file_is_named(self):
        os.makedirs(presets.preset_dir("stuck"))
        self.write("cfg/gifetch/presets/stuck/junk", "x")
        with self.assertRaisesRegex(GifetchError, "stuck already exists without preset.json"):
            cli.run(["--new", "-p", "stuck"])

    def test_c_must_be_a_file_on_a_normal_start_too(self):
        with self.assertRaisesRegex(GifetchError, "cannot read 'examples/8'"):
            cli.run(["-c", "examples/8"])

    def test_list_and_delete(self):
        cli.run(["--new", "-p", "card", "-rg", self.gif_folder()])
        cli.run(["-l"])
        self.assertIn("random from", self.printed(self.stdout))
        cli.run(["--delete", "card"])
        self.assertEqual(presets.names(), [])
        with self.assertRaisesRegex(GifetchError, "no preset named"):
            cli.run(["-d", "card"])

    # fastfetch passthrough

    def test_unknown_options_go_to_fastfetch(self):
        cli.run(["--new", "-p", "card"])
        cli.run(["--structure", "OS:Kernel", "-p", "card"])
        self.assertEqual(self.show.call_args.args[2], ["--structure", "OS:Kernel"])

    def test_standalone_options_and_no_kitty_run_plain_fastfetch(self):
        for args in (["--version"], ["--Version"], ["--help"], ["--format", "json"], ["--format=json"],
                     ["--json"], ["-j", "true"], ["--dynamic-interval", "500"]):
            cli.run(args)
            self.replace_process.assert_called_with(sources.DEFAULT_CONFIG, args)
        self.replace_process.reset_mock()
        for args in (["--format", "default"], ["--json", "false"], ["-j", "false"], ["--key-padding-left", "-1"]):
            cli.run(args)
            self.show.assert_called_with(sources.DEFAULT_GIF, sources.DEFAULT_CONFIG, args)
        self.replace_process.assert_not_called()
        self.show.reset_mock()
        cli.can_draw_images.return_value = False
        cli.run(["-rg", "/no/gifs/needed/here"])
        self.replace_process.assert_called_with(sources.DEFAULT_CONFIG, [])
        self.show.assert_not_called()

    # Usage errors

    def test_usage_errors(self):
        for argv in (
            ["-g", TESTCARD, "-rg", "x"],
            ["-c", "a.jsonc", "-rc", "x"],
            ["--new", "-g", TESTCARD],
            ["--new", "-p", "x", "--structure", "OS"],
            ["-p", "x", "-g", TESTCARD],
            ["-l", "-p", "x"],
            ["-l", "--structure", "OS"],
            ["-d", "x", "-g", TESTCARD],
            ["--config", "a.jsonc"],
            ["--CONFIG=a.jsonc"],
            ["-rgsomewhere"],
        ):
            with self.assertRaises(SystemExit, msg=argv):
                cli.run(argv)

    def test_main_turns_errors_into_messages(self):
        with self.assertRaises(SystemExit) as exit:
            cli.main(["-p", "nope"])
        self.assertIn("no preset named 'nope'", str(exit.exception.code))


if __name__ == "__main__":
    unittest.main()


class ImageBoxTest(unittest.TestCase):
    WINDOW = kitty.Window(cols=140, rows=45, px_w=1400, px_h=900)  # 10 x 20 px cells

    def test_image_is_as_tall_as_the_text(self):
        self.assertEqual(render.image_box((200, 300), 23, self.WINDOW), (31, 23))

    def test_wide_image_is_capped_at_the_width_share(self):
        cols, rows = render.image_box((1000, 200), 23, self.WINDOW)
        self.assertEqual(cols, 56)
        self.assertEqual(rows, 6)

    def test_short_text_still_gets_min_rows_and_window_limits_height(self):
        self.assertEqual(render.image_box((100, 100), 2, self.WINDOW)[1], render.MIN_ROWS)
        self.assertEqual(render.image_box((100, 400), 80, self.WINDOW), (22, 44))

    def test_tiny_window_never_gets_an_image_taller_than_itself(self):
        tiny = kitty.Window(cols=200, rows=5, px_w=2000, px_h=100)
        self.assertEqual(render.image_box((100, 100), 20, tiny)[1], 4)

    def test_image_makes_room_for_the_text_down_to_min_share(self):
        narrow = kitty.Window(cols=100, rows=45, px_w=1000, px_h=900)
        cols, rows = render.image_box((400, 300), 23, narrow, text_cols=62)
        self.assertEqual(cols, 100 - 1 - render.GAP - 62)
        self.assertLess(rows, 23)
        cols, _ = render.image_box((400, 300), 23, narrow, text_cols=95)
        self.assertEqual(cols, int(100 * render.MIN_WIDTH_SHARE))
        tiny = kitty.Window(cols=30, rows=45, px_w=300, px_h=900)
        self.assertEqual(render.image_box((400, 300), 23, tiny, text_cols=95)[0], render.MIN_COLS)

    def test_fit_line_cuts_visible_text_and_keeps_escapes(self):
        line = "\x1b[1mkey\x1b[0m  some long value"
        cut = render.fit_line(line, 8)
        self.assertEqual(render.visible_width(cut), 8)
        self.assertTrue(cut.endswith("\u2026"))
        self.assertIn("\x1b[1m", cut)
        self.assertEqual(render.fit_line(line, 80), line)

    def test_fit_line_counts_terminal_cells(self):
        self.assertEqual(render.visible_width(render.fit_line("\u65e5\u672c\u8a9e\u30c6\u30ad", 6)), 5)
        self.assertEqual(render.fit_line("e\u0301e\u0301e\u0301e\u0301", 3), "e\u0301e\u0301\u2026")
        self.assertEqual(render.fit_line("abc", 0), "")

    def test_fit_line_never_cuts_inside_an_escape(self):
        link = "\x1b]8;;http://x\x1b\\link text here\x1b]8;;\x1b\\"
        cut = render.fit_line(link, 5)
        self.assertEqual(render.visible_width(cut), 5)
        self.assertTrue(cut.endswith("\x1b]8;;\x1b\\"))

    def test_visible_width_ignores_colour_escapes(self):
        self.assertEqual(render.visible_width("\x1b[38;2;1;2;3mos\x1b[0m  CachyOS"), len("os  CachyOS"))

    def test_testcard_size_is_read(self):
        width, height = images.size(TESTCARD)
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)


class ProcessNameTest(unittest.TestCase):
    def test_process_is_renamed_for_fastfetch(self):
        import threading
        from gifetch import fastfetch

        def rename_and_read(result):
            fastfetch._hide_from_shell_detection()
            with open(f"/proc/self/task/{threading.get_native_id()}/comm") as f:
                result.append(f.read().strip())

        result = []  # a thread, so the test runner's own name stays as it is
        thread = threading.Thread(target=rename_and_read, args=(result,))
        thread.start()
        thread.join()
        self.assertEqual(result, ["fastfetch"])


class ReleaseTest(unittest.TestCase):
    """Frames named in icat's shared memory commands are deleted when they were never sent."""

    def test_release_deletes_named_frames_only(self):
        with tempfile.TemporaryDirectory() as shm:
            names = ["icat-AAAA1", "icat-BBBB22"]
            for name in names + ["unrelated"]:
                open(os.path.join(shm, name), "w").close()
            # icat sends the names base64 encoded without padding, prefixed with '/'.
            encoded = [base64.b64encode(f"/{n}".encode()).decode().rstrip("=") for n in names]
            escapes = (
                f"\x1b_Ga=T,q=2,t=s,s=4,v=4,i=7;{encoded[0]}\x1b\\"
                f"\x1b_Ga=f,q=2,t=s,s=4,v=4,i=7,z=80;{encoded[1]}\x1b\\"
                "\x1b_Ga=a,v=1,r=1,i=7,z=80\x1b\\"
            )
            kitty.release(escapes, shm_dir=shm)
            kitty.release(escapes, shm_dir=shm)  # already gone: no error
            self.assertEqual(os.listdir(shm), ["unrelated"])

    def test_zero_size_pty_falls_back(self):
        with mock.patch("fcntl.ioctl", return_value=bytes(8)), mock.patch("builtins.open", mock.mock_open()):
            win = kitty.window()
        self.assertEqual((win.cols, win.rows), kitty.FALLBACK_WINDOW)
        self.assertGreater(win.cell_w, 0)


class ImageSizeTest(unittest.TestCase):
    def test_every_format_reports_its_size(self):
        for name in sorted(os.listdir(FIXTURES)):
            with self.subTest(name):
                self.assertEqual(images.size(os.path.join(FIXTURES, name)), (40, 30))

    def size_of(self, data):
        with tempfile.NamedTemporaryFile() as f:
            f.write(data)
            f.flush()
            return images.size(f.name)

    def fixture(self, name):
        with open(os.path.join(FIXTURES, name), "rb") as f:
            return f.read()

    def test_jpeg_segments_far_beyond_the_first_kilobytes(self):
        jpeg = self.fixture("sample.jpg")
        big = b"".join(b"\xff\xe2" + (60002).to_bytes(2, "big") + bytes(60000) for _ in range(3))  # 180 KB
        self.assertEqual(self.size_of(jpeg[:2] + big + jpeg[2:]), (40, 30))

    def test_jpeg_fill_bytes_and_standalone_markers(self):
        jpeg = self.fixture("sample.jpg")
        self.assertEqual(self.size_of(jpeg[:2] + b"\xff\xff\xff\x01\xff\xd0" + jpeg[2:]), (40, 30))

    def test_jpeg_exif_orientation_swaps_the_size(self):
        jpeg = self.fixture("sample.jpg")
        tiff = b"II*\x00" + (8).to_bytes(4, "little") + (1).to_bytes(2, "little") \
            + struct.pack("<HHIHH", 0x0112, 3, 1, 6, 0) + bytes(4)  # IFD0: Orientation = 6
        body = b"Exif\x00\x00" + tiff
        app1 = b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body
        self.assertEqual(self.size_of(jpeg[:2] + app1 + jpeg[2:]), (30, 40))

    def test_truncated_or_forged_headers_are_refused(self):
        png, lossless, animated, lossy = (self.fixture(n) for n in ("sample.png", "lossless.webp", "animated.webp", "lossy.webp"))
        for label, data in (
            ("png without IHDR", png[:12] + b"cHRM" + png[16:]),
            ("cut VP8X", animated[:22]),
            ("cut VP8L", lossless[:22]),
            ("VP8L without signature", lossless[:20] + b"\x00" + lossless[21:]),
            ("VP8 without start code", lossy[:23] + bytes(3) + lossy[26:]),
            ("jpeg cut in the frame", self.fixture("sample.jpg")[:4]),
        ):
            with self.subTest(label), self.assertRaisesRegex(GifetchError, "no image size"):
                self.size_of(data)
