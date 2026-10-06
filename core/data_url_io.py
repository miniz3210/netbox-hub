"""Helpers for converting browser-provided data: URLs into in-memory file objects.

The clipboard paste listener captures screenshots as base64 ``data:image/...`` URLs. These
are reconstructed into upload-like objects so they can be merged with ``st.file_uploader``
output and fed to the AI Vision pipeline.
"""

import base64
import io


class DataUrlFile(io.BytesIO):
    """Minimal upload-like wrapper around a base64 data URL image.

    Exposes the subset of the Streamlit ``UploadedFile`` interface consumed by the preview and
    AI Vision code paths: ``getvalue()``, ``read()``, ``name`` and ``type``.
    """

    def __init__(self, data_url: str, name: str = "clipboard_image.png"):
        header, _, b64 = data_url.partition(",")
        mime = "image/png"
        if header.startswith("data:") and ";" in header:
            mime = header[len("data:"):].split(";")[0]
        raw = base64.b64decode(b64)
        super().__init__(raw)
        self.name = name
        self.type = mime


def data_url_to_uploadedfile(data_url: str, name: str = "clipboard_image.png") -> DataUrlFile:
    """Convert a ``data:image/...;base64,...`` string to a :class:`DataUrlFile`."""
    return DataUrlFile(data_url, name=name)
