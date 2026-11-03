from contextlib import ExitStack
from functools import lru_cache
import os
import threading

from yt_dlp import YoutubeDL
from yt_dlp.downloader import get_suitable_downloader
from yt_dlp.downloader.fragment import FragmentFD

from broader_vids.evidence.media import (
    MAX_VIDEO_RETRIES,
    EvidencePending,
    file_in_use,
    public_error,
)


class _ClosingFragmentFiles:
    def download(self, *args, **kwargs):
        self._owned_files = []
        self._owned_files_lock = threading.Lock()
        try:
            return super().download(*args, **kwargs)
        finally:
            with self._owned_files_lock:
                streams, self._owned_files = self._owned_files, []
            with ExitStack() as closing:
                for stream in streams:
                    if not stream.closed:
                        closing.callback(stream.close)

    def try_rename(self, old_filename, new_filename):
        if old_filename == new_filename:
            return
        for number in range(1 + MAX_VIDEO_RETRIES):
            try:
                os.replace(old_filename, new_filename)
                return
            except OSError as error:
                if not file_in_use(error):
                    raise
                if number == MAX_VIDEO_RETRIES:
                    raise EvidencePending(
                        f"The downloaded file remains in use after three retries: {public_error(error)}",
                        category="media_transfer",
                    ) from None
                self.params["_broader_vids_file_retry"](number)

    def sanitize_open(self, *args, **kwargs):
        stream, filename = super().sanitize_open(*args, **kwargs)
        with self._owned_files_lock:
            self._owned_files = [
                handle for handle in self._owned_files if not handle.closed
            ]
            self._owned_files.append(stream)
        return stream, filename


@lru_cache(maxsize=16)
def _closing_downloader(downloader):
    return type(
        f"Closing{downloader.__name__}", (_ClosingFragmentFiles, downloader), {}
    )


class ManagedYoutubeDL(YoutubeDL):
    def dl(self, name, info, subtitle=False, test=False):
        if test or not info.get("url"):
            return super().dl(name, info, subtitle=subtitle, test=test)
        downloader = get_suitable_downloader(info, self.params, to_stdout=(name == "-"))
        if not issubclass(downloader, FragmentFD) or name == "-":
            return super().dl(name, info, subtitle=subtitle, test=test)
        # Fragment downloaders must close their output even when a progress hook interrupts them.
        fd = _closing_downloader(downloader)(self, self.params)
        for hook in self._progress_hooks:
            fd.add_progress_hook(hook)
        new_info = self._copy_infodict(info)
        if new_info.get("http_headers") is None:
            new_info["http_headers"] = self._calc_headers(new_info)
        return fd.download(name, new_info, subtitle)
