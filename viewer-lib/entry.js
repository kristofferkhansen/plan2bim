// What plan2bim/viewer.html uses of three.js and of N8AO. `build.mjs` makes one file of it: the global `THREE`.
// Each name is listed: a file with all of three.js is a fifth larger, and each viewer holds this file.
export {
  AlwaysStencilFunc, BackSide, Box3, BufferAttribute, BufferGeometry, CircleGeometry, Color, DecrementWrapStencilOp,
  DepthStencilFormat, DepthTexture, DirectionalLight, DoubleSide, FrontSide, Group, HemisphereLight,
  IncrementWrapStencilOp, InstancedBufferAttribute, LineBasicMaterial, LineSegments, MathUtils, Mesh,
  MeshBasicMaterial, MeshStandardMaterial,
  NotEqualStencilFunc, OrthographicCamera, PCFShadowMap, PerspectiveCamera, Plane, PlaneGeometry, Raycaster, REVISION,
  ReplaceStencilOp, Scene, ShadowMaterial, SRGBColorSpace, UnsignedInt248Type, Vector2, Vector3, WebGLRenderer,
  WebGLRenderTarget,
} from "three";
export { LineMaterial } from "three/addons/lines/LineMaterial.js";
export { LineSegments2 } from "three/addons/lines/LineSegments2.js";
export { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
// the source file and not the package entry: the entry also needs the package `postprocessing`, which the viewer does not use
export { N8AOPass } from "n8ao/src/N8AOPass.js";
