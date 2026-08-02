from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import shutil
import sys
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "dw.py"
TEST_ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("dw", MODULE_PATH)
assert SPEC and SPEC.loader
dw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dw
SPEC.loader.exec_module(dw)


@contextlib.contextmanager
def writable_test_directory():
    """Avoid Python 3.13's Windows 0o700 ACL, which blocks sandboxed test subprocesses."""
    path = TEST_ROOT / f".dw-test-{uuid.uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path)


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
    filesize: int | None = None,
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
        "filesize": filesize,
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


class ShareTextTests(unittest.TestCase):
    XHS_URL = (
        "https://www.xiaohongshu.com/discovery/item/6a6d5495000000002403c0c8"
        "?source=webshare&xhsshare=pc_web&xsec_token=token=&xsec_source=pc_share"
    )

    def test_extracts_xiaohongshu_url_from_full_share_text(self) -> None:
        text = f"87 【标题 - 作者 | 小红书】 😆 分享码 😆 {self.XHS_URL}"
        self.assertEqual(dw.extract_urls_from_text(text), [self.XHS_URL])

    def test_trailing_emoji_is_not_included_in_url(self) -> None:
        text = f"完整分享文案 {self.XHS_URL}😆复制后打开"
        self.assertEqual(dw.extract_urls_from_text(text), [self.XHS_URL])

    def test_concatenated_and_markdown_duplicate_urls_are_deduplicated(self) -> None:
        text = f"[{self.XHS_URL}{self.XHS_URL}]({self.XHS_URL})"
        self.assertEqual(dw.extract_urls_from_text(text), [self.XHS_URL])

    def test_html_escaped_query_is_restored(self) -> None:
        escaped = self.XHS_URL.replace("&", "&amp;")
        self.assertEqual(dw.extract_urls_from_text(escaped), [self.XHS_URL])

    def test_douyin_modal_share_url_is_normalized(self) -> None:
        text = "分享 https://www.douyin.com/jingxuan?modal_id=7661530014969969381 复制"
        self.assertEqual(
            dw.extract_urls_from_text(text),
            ["https://www.douyin.com/video/7661530014969969381"],
        )

    def test_douyin_short_share_url_is_preserved(self) -> None:
        text = "8.74 nqr:/ #恋爱脑 https://v.douyin.com/3qaI6648IrI/ 复制此链接"
        self.assertEqual(
            dw.extract_urls_from_text(text),
            ["https://v.douyin.com/3qaI6648IrI/"],
        )

    def test_multiple_different_urls_remain_selectable(self) -> None:
        text = "第一个 https://example.test/a 第二个 https://example.test/b"
        self.assertEqual(
            dw.extract_urls_from_text(text),
            ["https://example.test/a", "https://example.test/b"],
        )


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

    def test_video_sorting_prioritizes_h264_then_estimated_size(self) -> None:
        values = [
            video("h265-large", "mp4", 2160, vcodec="hvc1.1.6", filesize=50_000_000),
            video("h264-small", "mp4", 720, filesize=8_000_000),
            video("h264-large", "mp4", 1080, filesize=12_000_000),
        ]
        ordered = dw.video_formats({"formats": values})
        self.assertEqual(
            [item["format_id"] for item in ordered],
            ["h264-large", "h264-small", "h265-large"],
        )

    def test_explicitly_watermarked_variant_is_hidden_when_clean_stream_exists(self) -> None:
        values = [
            video("download_addr", "mp4", 1080, note="Download video, watermarked"),
            video("play_addr", "mp4", 1080, note="Direct video"),
        ]
        self.assertEqual(
            [item["format_id"] for item in dw.video_formats({"formats": values})],
            ["play_addr"],
        )

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

    def test_non_live_download_uses_eight_way_native_and_aria2_downloaders(self) -> None:
        selection = dw.ItemSelection(
            video=video("v1", "mp4", 1080),
            audios=[audio("a1", "zh", codec="aac")],
            use_embedded_audio=False,
            replace_embedded_audio=False,
            container_mode="mp4",
            resolved_container="mp4",
            thumbnails=[],
        )
        args = dw.build_download_args(
            "https://example.test/video",
            selection,
            Path("/tmp/task"),
            Path("/tmp/task/result.txt"),
            False,
        )
        self.assertEqual(args[args.index("--concurrent-fragments") + 1], "8")
        downloader_values = [args[index + 1] for index, value in enumerate(args) if value == "--downloader"]
        self.assertEqual(downloader_values, ["aria2c", "dash,m3u8:native"])
        aria_arguments = args[args.index("--downloader-args") + 1]
        self.assertIn("-x 8", aria_arguments)
        self.assertIn("-s 8", aria_arguments)

    def test_live_download_keeps_native_downloader(self) -> None:
        selection = dw.ItemSelection(
            video=video("v1", "mp4", 1080, acodec="aac"),
            audios=[],
            use_embedded_audio=True,
            replace_embedded_audio=False,
            container_mode="mp4",
            resolved_container="mp4",
            thumbnails=[],
        )
        args = dw.build_download_args(
            "https://example.test/live",
            selection,
            Path("/tmp/task"),
            Path("/tmp/task/result.txt"),
            True,
        )
        self.assertEqual(args[args.index("--concurrent-fragments") + 1], "8")
        self.assertNotIn("--downloader", args)

    def test_container_menu_enter_defaults_to_auto(self) -> None:
        selected_video = video("v", "mp4", 1080, acodec="aac")
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(dw.ask_container(selected_video, [], True), ("auto", "mp4"))

    def test_matching_extension_is_still_remuxed_to_strip_metadata(self) -> None:
        selection = dw.ItemSelection(
            video=video("v", "mp4", 1080, acodec="aac"),
            audios=[],
            use_embedded_audio=True,
            replace_embedded_audio=False,
            container_mode="auto",
            resolved_container="mp4",
            thumbnails=[],
        )
        with writable_test_directory() as temp:
            source = Path(temp) / "source.mp4"
            source.write_bytes(b"media")
            result = mock.Mock(returncode=0, stderr="")
            with mock.patch.object(dw.subprocess, "run", return_value=result) as run:
                target = dw.ensure_final_container(source, selection)
        command = run.call_args.args[0]
        self.assertIn("-map_metadata", command)
        self.assertIn("-map_chapters", command)
        self.assertIn("copy", command)
        self.assertEqual(target.suffix, ".mp4")


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
        ), mock.patch.object(dw.sys, "stderr", stderr), mock.patch.object(
            dw, "disable_windows_quick_edit"
        ) as disable_quick_edit:
            dw.configure_console()

        disable_quick_edit.assert_called_once_with()
        stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_download_process_cannot_pause_waiting_for_console_input(self) -> None:
        process = mock.Mock()
        process.stderr = io.StringIO("")
        process.wait.return_value = 0
        with mock.patch.object(dw.subprocess, "Popen", return_value=process) as popen:
            result = dw.run_download_process(["yt-dlp", "url"], live=False)
        self.assertEqual(result.returncode, 0)
        self.assertIs(popen.call_args.kwargs["stdin"], dw.subprocess.DEVNULL)

    def test_ytdlp_environment_can_find_portable_aria2(self) -> None:
        with mock.patch.dict(dw.os.environ, {"PATH": "existing-path"}, clear=False):
            environment = dw.ytdlp_environment()
        self.assertEqual(environment["PATH"].split(dw.os.pathsep)[0], str(dw.BIN_DIR))

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

    def test_dependency_download_reports_percentage_size_and_speed(self) -> None:
        payload = b"progress-test" * 100
        source = mock.Mock()
        source.headers = {"Content-Length": str(len(payload))}
        source.read = io.BytesIO(payload).read
        output = io.BytesIO()
        console = io.StringIO()
        with mock.patch.object(dw.sys, "stdout", console):
            copied = dw.copy_response_with_progress(source, output, "asset.zip")
        self.assertEqual(copied, len(payload))
        self.assertEqual(output.getvalue(), payload)
        self.assertIn("asset.zip", console.getvalue())
        self.assertIn("100.0%", console.getvalue())
        self.assertIn("/s", console.getvalue())

    def test_dependency_download_rejects_incomplete_content_length(self) -> None:
        payload = b"short"
        source = mock.Mock()
        source.headers = {"Content-Length": str(len(payload) + 1)}
        source.read = io.BytesIO(payload).read
        with mock.patch.object(dw.sys, "stdout", io.StringIO()):
            with self.assertRaisesRegex(OSError, "incomplete download"):
                dw.copy_response_with_progress(source, io.BytesIO(), "asset.zip")

    def test_custom_github_mirror_is_tried_before_official_and_builtins(self) -> None:
        with mock.patch.dict(
            dw.os.environ,
            {"DW_GITHUB_MIRRORS": "https://mirror.example/{url}"},
            clear=False,
        ), mock.patch.object(dw, "GITHUB_MIRRORS_FILE", Path("missing-mirrors.txt")):
            candidates = dw.github_request_candidates(
                "https://api.github.com/repos/example/project/releases/latest"
            )
        self.assertEqual(
            candidates[0][0],
            "https://mirror.example/https://api.github.com/repos/example/project/releases/latest",
        )
        self.assertTrue(candidates[0][2])
        self.assertEqual(candidates[1][1], "GitHub 官方源")

    def test_invalid_custom_mirror_is_rejected_with_required_message(self) -> None:
        dw._WARNED_CUSTOM_MIRRORS.clear()
        with mock.patch.dict(dw.os.environ, {"DW_GITHUB_MIRRORS": "http://unsafe.example"}), mock.patch.object(
            dw, "GITHUB_MIRRORS_FILE", Path("missing-mirrors.txt")
        ), mock.patch.object(dw, "warn") as warning:
            self.assertEqual(dw.configured_github_mirrors(), [])
        warning.assert_called_once_with("你提供的镜像域名不可使用：http://unsafe.example")

    def test_domestic_sites_use_direct_policy(self) -> None:
        self.assertTrue(dw.is_domestic_url("https://v.douyin.com/example/"))
        self.assertTrue(dw.is_domestic_url("https://www.xiaohongshu.com/explore/id"))
        self.assertTrue(dw.is_domestic_url("https://example.cn/video"))
        self.assertFalse(dw.is_domestic_url("https://www.youtube.com/watch?v=x"))

    def test_direct_mode_never_reads_browser_cookie_database(self) -> None:
        policy = dw.RequestPolicy(direct=True, cookie_mode="none")
        args = dw.ytdlp_base_args(policy, "https://www.douyin.com/video/123")
        self.assertEqual(args[args.index("--proxy") + 1], "")
        self.assertNotIn("--cookies-from-browser", args)

    def test_cookie_menu_enter_defaults_to_manual_file(self) -> None:
        with mock.patch.object(dw, "cookie_file_available", return_value=True), mock.patch(
            "builtins.input", return_value=""
        ):
            self.assertEqual(dw.choose_cookie_mode(["https://www.douyin.com/video/123"]), "file")

    def test_xinpianchang_impersonation_also_applies_to_manual_cookies(self) -> None:
        policy = dw.RequestPolicy(direct=True, cookie_mode="file")
        with mock.patch.object(dw, "IS_WINDOWS", True), mock.patch.object(
            dw, "cookie_file_available", return_value=True
        ):
            args = dw.ytdlp_base_args(policy, "https://www.xinpianchang.com/a123")
        self.assertEqual(args[args.index("--impersonate") + 1], "chrome:windows-10")
        self.assertEqual(args[args.index("--cookies") + 1], str(dw.COOKIE_FILE))

    def test_proxy_retry_requires_confirmation(self) -> None:
        policy = dw.RequestPolicy(direct=True, cookie_mode="none", xpc_assist_attempted=True)
        failure = dw.DwError("读取失败", "HTTP Error 403: Forbidden")
        with mock.patch.object(dw, "ask_yes_no", return_value=False) as ask:
            self.assertIsNone(dw.next_request_policy("https://www.xinpianchang.com/a1", policy, failure))
        ask.assert_called_once_with("是否使用系统代理重试", default=False)

    def test_xinpianchang_failure_offers_browser_assist_before_proxy(self) -> None:
        policy = dw.RequestPolicy(direct=True, cookie_mode="file")
        failure = dw.DwError("读取失败", "HTTP Error 403: Forbidden")
        with mock.patch.object(dw, "ask_yes_no", return_value=False) as ask, mock.patch.object(
            dw, "warn"
        ) as warning:
            replacement = dw.next_request_policy(
                "https://www.xinpianchang.com/a12303964",
                policy,
                failure,
        )
        self.assertIsNone(replacement)
        self.assertEqual(
            ask.call_args_list,
            [
                mock.call("是否启动新片场 Chrome 辅助模式（推荐）", default=False),
                mock.call("是否使用系统代理重试", default=False),
            ],
        )
        self.assertTrue(any("仅导入本次临时 Chrome" in call.args[0] for call in warning.call_args_list))

    def test_xinpianchang_browser_assist_returns_task_scoped_info(self) -> None:
        policy = dw.RequestPolicy(direct=True, cookie_mode="file")
        failure = dw.DwError("读取失败", "HTTP Error 406: Not Acceptable")
        assisted = {"id": "a123", "formats": [{"format_id": "xpc-1"}]}
        with mock.patch.object(dw, "IS_WINDOWS", True), mock.patch.object(
            dw, "ask_yes_no", return_value=True
        ) as ask, mock.patch.object(
            dw, "xinpianchang_browser_assist", return_value=assisted
        ) as bridge:
            replacement = dw.next_request_policy(
                "https://www.xinpianchang.com/a123",
                policy,
                failure,
            )
        self.assertIsNotNone(replacement)
        assert replacement is not None
        self.assertTrue(replacement.xpc_assist_attempted)
        self.assertEqual(replacement.xpc_info, assisted)
        ask.assert_called_once_with("是否启动新片场 Chrome 辅助模式（推荐）", default=False)
        bridge.assert_called_once_with("https://www.xinpianchang.com/a123", policy)

    def test_xinpianchang_progressive_metadata_is_preserved(self) -> None:
        formats = dw.xinpianchang_progressive_formats(
            [
                {
                    "profile": "高清 1080p",
                    "mime": "video/mp4",
                    "bitrate": 4_012_010,
                    "fps": "25/1",
                    "codecName": "h264 / aac",
                    "filesize": 108_249_570,
                    "width": 1920,
                    "height": 816,
                    "url": "https://cdn.example/video.mp4",
                },
                {"profile": "不可下载", "width": 3840, "height": 1634, "url": None},
            ],
            "https://www.xinpianchang.com/a123",
        )
        self.assertEqual(len(formats), 1)
        self.assertEqual(formats[0]["vcodec"], "h264")
        self.assertEqual(formats[0]["acodec"], "aac")
        self.assertEqual(formats[0]["fps"], 25)
        self.assertAlmostEqual(formats[0]["tbr"], 4012.01)
        self.assertEqual(formats[0]["http_headers"]["Range"], "bytes=0-")

    def test_xinpianchang_temporary_cookies_keep_domain_and_path_scope(self) -> None:
        bridge = {
            "cookies": [
                {"name": "auth", "value": "good", "domain": ".xinpianchang.com", "path": "/"},
                {"name": "www", "value": "wrong-host", "domain": ".www.xinpianchang.com", "path": "/"},
                {"name": "hostonly", "value": "wrong-host", "domain": "xinpianchang.com", "path": "/"},
                {"name": "path", "value": "wrong-path", "domain": ".xinpianchang.com", "path": "/account"},
                {"name": "expired", "value": "old", "domain": ".xinpianchang.com", "path": "/", "expires": 1},
                {"name": "bad", "value": "line\r\nbreak", "domain": ".xinpianchang.com", "path": "/"},
            ]
        }
        header = dw.xinpianchang_api_cookie_header(
            bridge,
            "https://mod-api.xinpianchang.com/mod/api/v2/media/vid?appKey=key",
        )
        self.assertEqual(header, "auth=good")

    def test_xinpianchang_download_uses_task_info_json(self) -> None:
        selected_video = video("xpc-1080", "mp4", 1080, acodec="aac")
        selection = dw.ItemSelection(
            video=selected_video,
            audios=[],
            use_embedded_audio=True,
            replace_embedded_audio=False,
            container_mode="mp4",
            resolved_container="mp4",
            thumbnails=[],
        )
        assisted = {
            "id": "a123",
            "title": "测试",
            "formats": [selected_video],
            "_dw_xpc_browser_assisted": True,
        }
        with writable_test_directory() as temp:
            task_dir = Path(temp)
            args = dw.build_download_args(
                "https://www.xinpianchang.com/a123",
                selection,
                task_dir,
                task_dir / "result.txt",
                False,
                dw.RequestPolicy(direct=True, cookie_mode="file", xpc_info=assisted),
                assisted,
            )
            info_path = Path(args[args.index("--load-info-json") + 1])
            payload = dw.read_json(info_path, {})
        self.assertNotIn("https://www.xinpianchang.com/a123", args)
        self.assertNotIn("_dw_xpc_browser_assisted", payload)
        self.assertEqual(payload["formats"][0]["format_id"], "xpc-1080")

    def test_cookie_failure_does_not_retry_with_unreliable_browser_database(self) -> None:
        policy = dw.RequestPolicy(direct=True, cookie_mode="file")
        failure = dw.DwError("读取失败", "Fresh cookies needed")
        with mock.patch.object(dw, "ask_yes_no") as ask:
            replacement = dw.next_request_policy(
                "https://www.douyin.com/video/123",
                policy,
                failure,
            )
        self.assertIsNone(replacement)
        ask.assert_not_called()

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
        with writable_test_directory() as temp:
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
        with writable_test_directory() as temp:
            root = Path(temp)
            app = root / "app"
            command = app / "command"
            state = root / "state"
            alias = root / "WindowsApps" / "dw.cmd"
            helper = app / "uninstall-windows.ps1"
            app.mkdir()
            helper.write_text("# dw-managed-windows-cleanup\n", encoding="utf-8")
            with mock.patch.multiple(
                dw,
                APP_DIR=app,
                STATE_DIR=state,
                COMMAND_DIR=command,
                WINDOWS_ALIAS_LAUNCHER=alias,
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
            self.assertEqual(arguments[arguments.index("-AliasLauncher") + 1], str(alias))

    def test_update_source_is_validated(self) -> None:
        with writable_test_directory() as temp:
            config = Path(temp) / "update-source.json"
            config.write_text(
                '{"repository":"owner/project","ref":"release/v1.3.0"}',
                encoding="utf-8",
            )
            with mock.patch.object(dw, "UPDATE_CONFIG_FILE", config):
                self.assertEqual(dw.update_source(), ("owner/project", "release/v1.3.0"))
            config.write_text(
                '{"repository":"owner/project","ref":"../unsafe"}',
                encoding="utf-8",
            )
            with mock.patch.object(dw, "UPDATE_CONFIG_FILE", config), self.assertRaises(dw.DwError):
                dw.update_source()

    def test_windows_update_is_started_with_validated_helper_and_source(self) -> None:
        with writable_test_directory() as temp:
            root = Path(temp)
            helper = root / "update-windows.ps1"
            installer = root / "state" / "update" / "install-windows.ps1"
            helper.write_text("# dw-managed-windows-update\n", encoding="utf-8")
            installer.parent.mkdir(parents=True)
            installer.write_text("# yt-dlp-dw Windows 10/11\n", encoding="utf-8")
            with mock.patch.multiple(
                dw,
                IS_WINDOWS=True,
                WINDOWS_UPDATE_HELPER=helper,
                STATE_DIR=root / "state",
            ), mock.patch.object(dw.shutil, "which", return_value="powershell.exe"), mock.patch.object(
                dw.subprocess, "Popen"
            ) as popen:
                dw.schedule_windows_update(installer, "owner/project", "main")
            arguments = popen.call_args.args[0]
            self.assertEqual(arguments[0], "powershell.exe")
            self.assertEqual(arguments[arguments.index("-Installer") + 1], str(installer))
            self.assertEqual(arguments[arguments.index("-Repository") + 1], "owner/project")
            self.assertEqual(arguments[arguments.index("-RepositoryRef") + 1], "main")

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell integration test")
    def test_windows_update_helper_executes_validated_installer_and_cleans_it(self) -> None:
        helper = MODULE_PATH.parents[1] / "windows" / "update-runner.ps1"
        with writable_test_directory() as temp:
            root = Path(temp)
            state = root / "state"
            update_dir = state / "update"
            update_dir.mkdir(parents=True)
            (state / ".dw-owned").write_text("owned by yt-dlp-dw\n", encoding="utf-8")
            result_file = root / "updated.txt"
            installer = update_dir / "install-windows.ps1"
            installer.write_text(
                "# yt-dlp-dw Windows 10/11\n"
                "[CmdletBinding()]\n"
                "param([string]$Repository,[string]$RepositoryRef,[string]$UpdateRef)\n"
                "[IO.File]::WriteAllText($env:DW_TEST_UPDATE_RESULT, "
                "\"$Repository@$RepositoryRef@$UpdateRef\")\n",
                encoding="ascii",
            )
            environment = dw.os.environ.copy()
            environment["DW_TEST_UPDATE_RESULT"] = str(result_file)
            result = dw.subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-ParentPid",
                    "2147483647",
                    "-Installer",
                    str(installer),
                    "-StateDir",
                    str(state),
                    "-Repository",
                    "owner/project",
                    "-RepositoryRef",
                    "test-branch",
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=dw.subprocess.PIPE,
                stderr=dw.subprocess.STDOUT,
                env=environment,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(result_file.read_text(encoding="utf-8"), "owner/project@test-branch@test-branch")
            self.assertFalse(installer.exists())
            self.assertFalse(update_dir.exists())


class FilesystemSafetyTests(unittest.TestCase):
    def test_cookie_file_requires_an_explicit_task_policy(self) -> None:
        with mock.patch.object(dw, "cookie_file_available", return_value=True):
            self.assertNotIn("--cookies", dw.ytdlp_base_args())
            explicit = dw.ytdlp_base_args(dw.RequestPolicy(cookie_mode="file"))
        self.assertEqual(explicit[explicit.index("--cookies") + 1], str(dw.COOKIE_FILE))

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
        with writable_test_directory() as temp:
            dw.OUTPUT_DIR = Path(temp)
            path, reason = dw.manifest_owned_file({"path": str(Path(temp).parent / "other.mp4")})
            self.assertIsNone(path)
            self.assertIn("范围外", reason)
        dw.OUTPUT_DIR = old_output

    def test_manifest_detects_replaced_inode(self) -> None:
        old_output = dw.OUTPUT_DIR
        with writable_test_directory() as temp:
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
        with writable_test_directory() as temp:
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
