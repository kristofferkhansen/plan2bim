"""Fetch public DWG files for `tests/test_samples.py`. They are not in the repository.

    uv run python tests/samples.py dwg/samples

The files are sample drawings of Autodesk and test drawings of LibreDWG, each from the server of its
publisher. A file whose hash is not the one below is not kept. A file that is there already is not
fetched again.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

AUTODESK = "https://download.autodesk.com/us"
LIBREDWG = "https://raw.githubusercontent.com/LibreDWG/libredwg/master/test/test-data"
SAMPLES = {     # path under the directory: address, SHA-256
    # AutoCAD sample files. Two storeys of a house in one drawing, in metres and in inches.
    "autodesk/blocks_and_tables_-_metric.dwg": (
        f"{AUTODESK}/samplefiles/acad/blocks_and_tables_-_metric.dwg",
        "7d5507da05fb70ba55875d99e676524ca2caa0ed7a5582ad130c3c06e8d925f1"),
    "autodesk/blocks_and_tables_-_imperial.dwg": (
        f"{AUTODESK}/samplefiles/acad/blocks_and_tables_-_imperial.dwg",
        "3c95e8a82d5c8c251483fabc61efa8ca904f4e4bc8c2fce4786d74e812284d07"),
    "autodesk/architectural_example-imperial.dwg": (
        f"{AUTODESK}/samplefiles/acad/architectural_example-imperial.dwg",
        "52d14a7bdb946099d3cf16fd276d19bd8924348fd02b2ddd0003cd4f6b34cce7"),
    "autodesk/architectural_-_annotation_scaling_and_multileaders.dwg": (
        f"{AUTODESK}/samplefiles/acad/architectural_-_annotation_scaling_and_multileaders.dwg",
        "0e74f0aa84b1323c922345f067739c42ee26213dea010b407b87a94212271654"),
    # AutoCAD LT sample file. All plans of a house, each in a block.
    "autodesk/home_floor_plan.dwg": (
        f"{AUTODESK}/support/files/autocadlt_2010_sample_files/home_floor_plan.dwg",
        "5d3fc31fac4f31acfb44ad9b19a24217e6700822c89e8057aebab322481a6187"),
    # One drawing of the LibreDWG tests, saved as each version of DWG from R14 to 2018.
    "libredwg/example_r14.dwg": (f"{LIBREDWG}/example_r14.dwg", "aec626c5680584c763418497495950089854ff9682af36076851fd793d9e2c0b"),
    "libredwg/example_2000.dwg": (f"{LIBREDWG}/example_2000.dwg", "34574244d7556d1ef7b437443d9b3d1ad8662e1c669c42d80cff6a8a19799be9"),
    "libredwg/example_2004.dwg": (f"{LIBREDWG}/example_2004.dwg", "e72d5e86d5d36d64b08822fb25a46079f592fd895a6157b1b8d9b07775e06108"),
    "libredwg/example_2007.dwg": (f"{LIBREDWG}/example_2007.dwg", "fe2d27229f67ae801b64cc01b50d4f7cdf53770de3e23640be9c24a14de5bc12"),
    "libredwg/example_2010.dwg": (f"{LIBREDWG}/example_2010.dwg", "457fb3d06cacd16dd945b7342651bebdefde03d7eca0d1bd5743c6730e436634"),
    "libredwg/example_2013.dwg": (f"{LIBREDWG}/example_2013.dwg", "ce80953e4881899cde8c42b97919feb2697259554ca54dc4005b19068a12c18f"),
    "libredwg/example_2018.dwg": (f"{LIBREDWG}/example_2018.dwg", "7bc7721224003062b237845842b7a976e46771c37eba6833b9ed3712e7a5a65a"),
}


def fetch(to: Path) -> int:
    """Fetch the files that are not in `to` yet. Returns the number of files that could not be had."""
    missing = 0
    for name, (url, sha256) in SAMPLES.items():
        path = to / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
            print(f"there      {name}")
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "plan2bim-tests"}), timeout=60) as r:
                data = r.read()
        except OSError as e:
            print(f"no answer  {name}: {e}")
            missing += 1
            continue
        if hashlib.sha256(data).hexdigest() != sha256:
            print(f"other file {name}: {url} does not give the file with SHA-256 {sha256}")
            missing += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"fetched    {name}")
    return missing


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit((__doc__ or "").strip())
    sys.exit(1 if fetch(Path(sys.argv[1])) else 0)
