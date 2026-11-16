# three.js and N8AO

The viewer uses three.js r187 (https://threejs.org) and N8AO 2.1.0 (https://github.com/N8python/n8ao), which
shades the corners. `three.bundle.min.js` holds the parts of the two that `plan2bim/viewer.html` uses, as one
script that sets the global `THREE`.

```sh
sha256sum plan2bim/vendor/three.bundle.min.js
# c858057ed8f0d700dbc24eeab1bc3bc6e457fcc33935fa0882a89f0c223980bb
```

`plan2bim/web.py` copies it into each `NAME.html`, so the viewer needs no network.

## How to make the file again

The file is made from `viewer-lib/` in the root of the repository. This needs Node.js and network access.
`plan2bim` itself, its tests and its container need neither: they use the file that is here.

```sh
cd viewer-lib
npm ci          # the versions in package-lock.json
npm run build   # writes plan2bim/vendor/three.bundle.min.js and prints its size and sha256
```

`viewer-lib/entry.js` lists each name that the viewer uses. Add a name there when the viewer needs one more,
make the file again, and write the new sha256 above.

## Licences

N8AO: CC0 1.0 Universal (the file `LICENSE` of the package; its `package.json` says ISC). It asks for no notice.

three.js:

The MIT License

Copyright © 2010-2026 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
