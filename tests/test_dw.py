from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "dw.py"
TEST_ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("dw", MODULE_PATH)
assert SPEC and SPEC.loader
dw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dw
SPEC.loader.exec_module(dw)


def video(
    format_id: str,
    ext: str,
    height: int,
    *,
    width: int | None = None,
    fps: int = 30,
    vcodec: str = "avc1.640028",
    acodec: str = "none",
    tbr: int = 1000,
    note: str = "",
    protocol: str = "https",
) -> dict:
    return {
        "format_id": format_id,
        "ext": ext,
        "width": width or int(height * 16 / 9),
        "height": height,
        "fps": fps,
        "vcodec": vcodec,
        "acodec": acodec,
        "tbr": tbr,
        "format_note": note,
        "protocol": protocol,
        "url": f"https://example.test/{format_id}",
    }


def audio(format_id: str, language: str, codec: str = "opus", abr: int = 128) -> dict:
    return {
        "format_id": format_id,
        "ext": "webm" if codec == "opus" else "m4a",
        "vcodec": "none",
        "acodec": codec,
        "language": language,
        "abr": abr,
        "asr": 48000,
        "audio_channels": 2,
        "url": f"https://example.test/{format_id}",
    }


class SelectionParserTests(unittest.TestCase):
    def test_mixed_selection_and_deduplication(self) -> None:
        self.assertEqual(dw.parse_number_selection("1,3,5-7,3", 10), [1, 3, 5, 6, 7])

    def test_all_selection(self) -> None:
        self.assertEqual(dw.parse_number_selection("全部", 4), [1, 2, 3, 4])

    def test_reversed_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dw.parse_number_selection("8-2", 10)

    def test_out_of_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dw.parse_number_selection("11", 10)


class FormatTests(unittest.TestCase):
    def test_video_sorting_groups_containers_then_quality(self) -> None:
        values = [
            video("w1080", "webm", 1080, vcodec="vp9"),
            video("m720", "mp4", 720),
            video("m1080", "mp4", 1080),
            video("x2160", "mkv", 2160),
            video("w720", "webm", 720, vcodec="vp9"),
        ]
        ordered = dw.video_formats({"formats": values})
        self.assertEqual([item["format_id"] for item in ordered], ["m1080", "m720", "w1080", "w720", "x2160"])

    def test_storyboards_are_hidden(self) -> None:
        values = [
            video("real", "mp4", 1080),
            video("story", "mhtml", 90, vcodec="images", note="storyboard", protocol="mhtml"),
        ]
        self.assertEqual([item["format_id"] for item in dw.video_formats({"formats": values})], ["real"])

    def test_audio_only_filter(self) -> None:
        formats = [audio("a1", "zh-CN"), video("combined", "mp4", 720, acodec="aac")]
        self.assertEqual([item["format_id"] for item in dw.audio_formats({"formats": formats})], ["a1"])

    def test_exact_and_lower_quality_matching(self) -> None:
        target_format = video("target", "mp4", 1080, fps=60, tbr=4000)
        target = dw.format_signature(target_format, "video")
        exact = video("exact", "mp4", 1080, fps=60, tbr=3900)
        matched, note = dw.match_video_format([video("low", "mp4", 720), exact], target)
        self.assertEqual(matched["format_id"], "exact")
        self.assertEqual(note, "完全匹配")

        fallback, note = dw.match_video_format(
            [video("webm1080", "webm", 1080, vcodec="vp9"), video("mp4720", "mp4", 720)],
            target,
        )
        self.assertEqual(fallback["format_id"], "mp4720")
        self.assertEqual(note, "最接近匹配")

    def test_matching_never_forces_higher_resolution(self) -> None:
        target = dw.format_signature(video("target", "mp4", 720), "video")
        matched, reason = dw.match_video_format([video("only", "mp4", 1080)], target)
        self.assertIsNone(matched)
        self.assertIn("不超过", reason)

    def test_multilanguage_audio_matching_reports_missing(self) -> None:
        targets = [
            dw.format_signature(audio("zh", "zh-CN"), "audio"),
            dw.format_signature(audio("en", "en"), "audio"),
        ]
        matched, missing = dw.match_audio_formats([audio("zh2", "zh")], targets)
        self.assertEqual([item["format_id"] for item in matched], ["zh2"])
        self.assertEqual(missing, ["en"])


class ContainerTests(unittest.TestCase):
    def test_mp4_compatibility(self) -> None:
        self.assertTrue(dw.codec_compatible("mp4", "avc1.640028", ["mp4a.40.2"])[0])
        self.assertFalse(dw.codec_compatible("mp4", "vp9", ["opus"])[0])

    def test_webm_compatibility(self) -> None:
        self.assertTrue(dw.codec_compatible("webm", "vp09.00.51.08", ["opus"])[0])
        self.assertFalse(dw.codec_compatible("webm", "avc1.640028", ["aac"])[0])

    def test_multi_audio_recommends_mkv(self) -> None:
        selected_video = video("v", "mp4", 1080)
        self.assertEqual(
            dw.recommended_container(selected_video, [audio("a", "zh"), audio("b", "en")], False),
            "mkv",
        )

    def test_embedded_audio_replacement_uses_safe_intermediate_mkv(self) -> None:
        selected_video = video("v1", "mp4", 1080, acodec="aac")
        selected_audio = audio("a1", "zh", codec="aac")
        selection = dw.ItemSelection(
            video=selected_video,
            audios=[selected_audio],
            use_embedded_audio=False,
            replace_embedded_audio=True,
            container_mode="mp4",
            resolved_container="mp4",
            thumbnails=[],
        )
        args = dw.build_download_args(
            "https://example.test/video", selection, Path("/tmp/task"), Path("/tmp/task/result.txt"), False
        )
        self.assertEqual(args[args.index("--format") + 1], "v1+a1")
        self.assertEqual(args[args.index("--merge-output-format") + 1], "mkv")
        self.assertEqual(args[args.index("--remux-video") + 1], "mkv")


class ThumbnailTests(unittest.TestCase):
    def test_exact_duplicates_are_removed_and_sorted(self) -> None:
        duplicate = {"id": "x", "url": "https://example.test/x.jpg", "width": 640, "height": 360}
        values = [
            duplicate,
            dict(duplicate),
            {"id": "large", "url": "https://example.test/l.jpg", "width": 1920, "height": 1080},
        ]
        result = dw.deduplicated_thumbnails({"thumbnails": values})
        self.assertEqual([item["id"] for item in result], ["large", "x"])
        self.assertEqual([item["_dw_index"] for item in result], [1, 2])

    def test_thumbnail_rule_matches_resolution_order(self) -> None:
        current = dw.deduplicated_thumbnails(
            {
                "thumbnails": [
                    {"url": "https://e/1", "width": 1280, "height": 720},
                    {"url": "https://e/2", "width": 640, "height": 360},
                ]
            }
        )
        targets = [{"width": 1920, "height": 1080, "filesize": None, "rank": 1}]
        self.assertEqual(dw.match_thumbnails(current, targets, False)[0]["width"], 1280)


class InteractiveFlowTests(unittest.TestCase):
    def test_initial_video_audio_container_and_all_thumbnail_selection(self) -> None:
        metadata = {
            "formats": [video("v", "mp4", 1080), audio("zh", "zh-CN", "aac"), audio("en", "en", "aac")],
            "thumbnails": [
                {"id": "large", "url": "https://example.test/l.jpg", "width": 1920, "height": 1080},
                {"id": "small", "url": "https://example.test/s.jpg", "width": 640, "height": 360},
            ],
        }
        with mock.patch("builtins.input", side_effect=["1", "1,2", "3", "y", "a"]):
            selection, profile = dw.choose_initial_selection(metadata)
        self.assertEqual(selection.video["format_id"], "v")
        self.assertEqual([item["format_id"] for item in selection.audios], ["en", "zh"])
        self.assertEqual(selection.resolved_container, "mkv")
        self.assertEqual(len(selection.thumbnails), 2)
        self.assertTrue(profile.all_thumbnails)

    def test_combined_video_can_replace_embedded_audio(self) -> None:
        metadata = {
            "formats": [video("combined", "mp4", 720, acodec="aac"), audio("external", "zh", "aac")],
            "thumbnails": [],
        }
        with mock.patch("builtins.input", side_effect=["1", "n", "1", "2", "n"]):
            selection, _profile = dw.choose_initial_selection(metadata)
        self.assertTrue(selection.replace_embedded_audio)
        self.assertFalse(selection.use_embedded_audio)
        self.assertEqual(selection.audios[0]["format_id"], "external")
        self.assertEqual(selection.resolved_container, "mp4")


class PlatformSupportTests(unittest.TestCase):
    def test_windows_console_is_configured_for_utf8(self) -> None:
        stdout = mock.Mock()
        stderr = mock.Mock()
        with mock.patch.object(dw, "IS_WINDOWS", True), mock.patch.object(
            dw.sys, "stdout", stdout
        ), mock.patch.object(dw.sys, "stderr", stderr):
            dw.configure_console()

        stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_dependency_http_query_retries_transient_failure(self) -> None:
        response = io.StringIO('{"ok": true}')
        with mock.patch.object(
            dw.urllib.request,
            "urlopen",
            side_effect=[urllib.error.URLError("temporary TLS failure"), response],
        ) as urlopen, mock.patch.object(dw.time, "sleep") as sleep:
            self.assertEqual(dw.fetch_json("https://example.test/release"), {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_dependency_asset_layouts(self) -> None:
        with mock.patch.object(dw, "IS_WINDOWS", True):
            self.assertEqual(dw.ytdlp_asset_name(), "yt-dlp.exe")
            self.assertEqual(
                dw.deno_asset_layout(),
                ("deno-x86_64-pc-windows-msvc.zip", "deno.exe"),
            )
            self.assertEqual(
                dw.ffmpeg_asset_layout(),
                ("ffmpeg-master-latest-win64-gpl.zip", {"ffmpeg.exe", "ffprobe.exe"}),
            )
        with mock.patch.object(dw, "IS_WINDOWS", False):
            self.assertEqual(dw.ytdlp_asset_name(), "yt-dlp_linux")
            self.assertEqual(
                dw.deno_asset_layout(),
                ("deno-x86_64-unknown-linux-gnu.zip", "deno"),
            )

    def test_process_group_options_are_platform_specific(self) -> None:
        with mock.patch.object(dw, "IS_WINDOWS", True):
            self.assertIn("creationflags", dw.process_group_options(True))
            self.assertNotIn("start_new_session", dw.process_group_options(True))
        with mock.patch.object(dw, "IS_WINDOWS", False):
            self.assertEqual(dw.process_group_options(True), {"start_new_session": True})
        self.assertEqual(dw.process_group_options(False), {})

    def test_forced_windows_stop_uses_taskkill_for_the_process_tree(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        process.pid = 4321
        with mock.patch.object(dw, "IS_WINDOWS", True), mock.patch.object(
            dw.subprocess, "run"
        ) as run:
            dw.signal_download_process(process, grouped=True, force=True)
        self.assertEqual(run.call_args.args[0], ["taskkill.exe", "/PID", "4321", "/T", "/F"])

    def test_instance_lock_rejects_a_second_instance(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            with mock.patch.multiple(
                dw,
                STATE_DIR=root,
                CACHE_DIR=root / "cache",
                TASKS_DIR=root / "tasks",
                LOCK_FILE=root / "dw.lock",
                STATE_MARKER=root / ".dw-owned",
            ):
                with dw.InstanceLock():
                    with self.assertRaises(dw.DwError):
                        with dw.InstanceLock():
                            pass

    def test_windows_cleanup_is_started_with_exact_managed_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            app = root / "app"
            command = app / "command"
            state = root / "state"
            helper = app / "uninstall-windows.ps1"
            app.mkdir()
            helper.write_text("# dw-managed-windows-cleanup\n", encoding="utf-8")
            with mock.patch.multiple(
                dw,
                APP_DIR=app,
                STATE_DIR=state,
                COMMAND_DIR=command,
                WINDOWS_UNINSTALL_HELPER=helper,
            ), mock.patch.object(dw.shutil, "which", return_value="powershell.exe"), mock.patch.object(
                dw.subprocess, "Popen"
            ) as popen:
                self.assertIsNone(dw.schedule_windows_cleanup())
            arguments = popen.call_args.args[0]
            self.assertEqual(arguments[0], "powershell.exe")
            self.assertEqual(arguments[arguments.index("-AppDir") + 1], str(app))
            self.assertEqual(arguments[arguments.index("-StateDir") + 1], str(state))
            self.assertEqual(arguments[arguments.index("-CommandDir") + 1], str(command))


class FilesystemSafetyTests(unittest.TestCase):
    def test_inaccessible_cookie_file_is_treated_as_absent(self) -> None:
        inaccessible = mock.Mock()
        inaccessible.is_file.side_effect = PermissionError("permission denied")
        with mock.patch.object(dw, "COOKIE_FILE", inaccessible):
            self.assertFalse(dw.cookie_file_available())
            self.assertNotIn("--cookies", dw.ytdlp_base_args())

    def test_filename_preserves_unicode_and_removes_unsafe_characters(self) -> None:
        value = dw.sanitize_filename('中文/日本語:*? "test"')
        self.assertIn("中文", value)
        self.assertIn("日本語", value)
        for character in '<>:"/\\|?*':
            self.assertNotIn(character, value)

    def test_filename_is_limited_by_utf8_bytes(self) -> None:
        value = dw.sanitize_filename("下载" * 200, max_bytes=30)
        self.assertLessEqual(len(value.encode("utf-8")), 30)

    def test_windows_reserved_device_names_are_prefixed(self) -> None:
        self.assertEqual(dw.sanitize_filename("CON"), "_CON")
        self.assertEqual(dw.sanitize_filename("lpt1.txt"), "_lpt1.txt")

    def test_checksum_parser_supports_named_and_hash_only_files(self) -> None:
        digest = hashlib.sha256(b"test").hexdigest()
        self.assertEqual(dw.expected_checksum(f"{digest} *asset.zip\n", "asset.zip"), digest)
        self.assertEqual(dw.expected_checksum(f"{digest}\n", "asset.zip"), digest)

    def test_checksum_parser_supports_deno_windows_format(self) -> None:
        digest = hashlib.sha256(b"deno").hexdigest().upper()
        content = (
            "Algorithm : SHA256\r\n"
            f"Hash      : {digest}\r\n"
            "Path      : C:\\a\\deno\\target\\release\\deno-x86_64-pc-windows-msvc.zip\r\n"
        )
        self.assertEqual(
            dw.expected_checksum(content, "deno-x86_64-pc-windows-msvc.zip"),
            digest.lower(),
        )

    def test_manifest_rejects_paths_outside_output_directory(self) -> None:
        old_output = dw.OUTPUT_DIR
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            dw.OUTPUT_DIR = Path(temp)
            path, reason = dw.manifest_owned_file({"path": str(Path(temp).parent / "other.mp4")})
            self.assertIsNone(path)
            self.assertIn("范围外", reason)
        dw.OUTPUT_DIR = old_output

    def test_manifest_detects_replaced_inode(self) -> None:
        old_output = dw.OUTPUT_DIR
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            dw.OUTPUT_DIR = Path(temp)
            owned = Path(temp) / "video.mp4"
            owned.write_bytes(b"video")
            current = owned.stat()
            path, reason = dw.manifest_owned_file(
                {"path": str(owned), "device": current.st_dev, "inode": current.st_ino + 1}
            )
            self.assertIsNone(path)
            self.assertIn("替换", reason)
        dw.OUTPUT_DIR = old_output

    def test_exclusive_move_never_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            source = root / "source.mp4"
            destination = root / "destination.mp4"
            source.write_bytes(b"new")
            destination.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                dw.move_file_exclusive(source, destination)
            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertEqual(source.read_bytes(), b"new")


if __name__ == "__main__":
    unittest.main()
