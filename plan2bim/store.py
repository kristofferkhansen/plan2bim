"""Where the web service keeps projects: a directory, or a blob container in Azure.

Both hold files by key. A key is a path with "/" between its parts. The service makes each key from parts
that it has checked, so no key leaves the store.

The blob container is reached with the identity of the process: a managed identity in Azure (its client id
is in `AZURE_CLIENT_ID`), or the account of `az login` on a developer's machine. There is no account key
and no connection string.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

CHUNK = 1 << 20


class Store(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes:
        """The content of `key`. Raises KeyError when there is no such key."""
        ...

    def chunks(self, key: str) -> Iterator[bytes]:
        """The content of `key` in pieces, for a file that is too large to hold in memory. Raises KeyError
        at the call, not at the first piece, when there is no such key."""
        ...

    def upload(self, key: str, path: Path) -> None: ...

    def download(self, key: str, path: Path) -> None: ...

    def keys(self, prefix: str) -> list[str]: ...

    def delete(self, prefix: str) -> None:
        """Remove every key that starts with `prefix`."""
        ...


class DirStore:
    """Projects in a directory. For one machine: development, tests, a server with a disk."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"key {key!r} is outside the store")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")      # a reader never sees half a file
        part.write_bytes(data)
        part.replace(path)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise KeyError(key) from None

    def chunks(self, key: str) -> Iterator[bytes]:
        try:
            f = self._path(key).open("rb")
        except FileNotFoundError:
            raise KeyError(key) from None

        def pieces() -> Iterator[bytes]:
            with f:
                while piece := f.read(CHUNK):
                    yield piece
        return pieces()

    def upload(self, key: str, path: Path) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    def download(self, key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), path)

    def keys(self, prefix: str) -> list[str]:
        return sorted(key for p in self.root.rglob("*") if p.is_file() and not p.name.endswith(".part")
                      and (key := p.relative_to(self.root).as_posix()).startswith(prefix))

    def delete(self, prefix: str) -> None:
        for key in self.keys(prefix):
            self._path(key).unlink()


class BlobStore:
    """Projects in one container of Azure Blob Storage."""

    def __init__(self, container) -> None:      # azure.storage.blob.ContainerClient
        self.container = container

    def put(self, key: str, data: bytes) -> None:
        self.container.upload_blob(key, data, overwrite=True)

    def get(self, key: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self.container.download_blob(key).readall()
        except ResourceNotFoundError:
            raise KeyError(key) from None

    def chunks(self, key: str) -> Iterator[bytes]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self.container.download_blob(key).chunks()
        except ResourceNotFoundError:
            raise KeyError(key) from None

    def upload(self, key: str, path: Path) -> None:
        with path.open("rb") as f:
            self.container.upload_blob(key, f, overwrite=True)

    def download(self, key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            self.container.download_blob(key).readinto(f)

    def keys(self, prefix: str) -> list[str]:
        return sorted(self.container.list_blob_names(name_starts_with=prefix))

    def delete(self, prefix: str) -> None:
        for key in self.keys(prefix):
            self.container.delete_blob(key)


def open_store(where: str) -> Store:
    """The store that `where` names: the URL of a blob container (https://ACCOUNT.blob.core.windows.net/CONTAINER),
    or a directory."""
    if where.startswith("https://"):
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        return BlobStore(ContainerClient.from_container_url(where, credential=DefaultAzureCredential()))
    return DirStore(Path(where))
