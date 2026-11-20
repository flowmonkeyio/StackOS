"""Offline downloader failure and cache tests; no native library is executed."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "fetch", Path(__file__).with_name("fetch-tdlib-input.py")
)
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


class DownloaderTests(unittest.TestCase):
    def test_unverified_archive_fails_before_extraction(self):
        with patch.object(fetch.tarfile, "open") as extract:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                fetch.verify_archive(b"untrusted")
            extract.assert_not_called()

    def test_verified_cache_is_offline_and_repairs_extracted_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "package.tgz").write_bytes(b"verified archive")
            (cache / "libtdjson.dylib").write_bytes(b"corrupt")
            with (
                patch.object(fetch, "verify_archive", return_value=b"verified library"),
                patch.object(fetch.urllib.request, "urlopen") as network,
            ):
                self.assertEqual(fetch.fetch(cache).read_bytes(), b"verified library")
                network.assert_not_called()

    def test_bad_cache_and_bad_download_never_replace_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "package.tgz").write_bytes(b"corrupt")
            (cache / "libtdjson.dylib").write_bytes(b"preserved")
            with patch.object(fetch.urllib.request, "urlopen") as network:
                response = network.return_value.__enter__.return_value
                response.url = fetch.URL
                response.read.return_value = b"also corrupt"
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    fetch.fetch(cache)
            self.assertEqual((cache / "libtdjson.dylib").read_bytes(), b"preserved")

    def test_redirect_rejected(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(fetch.urllib.request, "urlopen") as network,
        ):
            network.return_value.__enter__.return_value.url = "https://other.example/input"
            with self.assertRaisesRegex(ValueError, "redirect"):
                fetch.fetch(Path(temporary))


if __name__ == "__main__":
    unittest.main()
