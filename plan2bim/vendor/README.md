# three.js

The viewer uses three.js r149 (MIT licence, https://threejs.org). `plan2bim/web.py` reads it from
`plan2bim/vendor/three.min.js` and copies it into each `NAME.html`.

Download it once, from the repository root:

```sh
curl -L -o plan2bim/vendor/three.min.js https://unpkg.com/three@0.149.0/build/three.min.js
sha256sum plan2bim/vendor/three.min.js
# 8a5f7249903b54d30f79f708699d2fed2d6a1d0741a4cd41377d1f01bb5a2271
```
