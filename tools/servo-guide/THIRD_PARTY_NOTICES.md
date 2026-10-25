# Third-party notices / 第三方来源与许可

## CAD and derived model assets — CC BY-NC-SA 4.0

`site/model/` contains tessellated display meshes, instance transforms, part metadata and servo annotations derived from the Feetech MicroDuck CAD.

- CAD author: **机械行者Robo**.
- Publisher / adaptation project: **fanhao375**, https://github.com/fanhao375/microduck-replica-cad .
- Source release: https://github.com/fanhao375/microduck-replica-cad/releases/tag/v2.1 , `SolidWorks-FT2026-09-25.zip`.
- Underlying robot and model: **Pollen Robotics**, https://github.com/pollen-robotics/microduck_rl .
- Upstream license statement: https://github.com/fanhao375/microduck-replica-cad/blob/c9a533bc1206d8899950a5d466965a992d7e052a/README.md#声明与许可 .
- License: **Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International**, https://creativecommons.org/licenses/by-nc-sa/4.0/ . Full text: `site/licenses/CC-BY-NC-SA-4.0.txt`.

Changes by lxm: extracted native saved tessellations and assembly transforms, converted coordinate axes and units, added servo IDs, Chinese labels and assembly hints, omitted one part without a saved mesh. These derived model assets are distributed under the same CC BY-NC-SA 4.0 license. No endorsement by the original authors is implied. The software MIT license does not relicense these assets or permit commercial use of them.

## Three.js and OrbitControls — MIT

Three.js 0.160.0, https://github.com/mrdoob/three.js/tree/r160 . Copyright © 2010–2023 three.js authors. Original license preserved at `site/vendor/THREE-LICENSE.txt`.

## Extraction dependency

`scripts/build_model.py` optionally uses sldkit 0.2.0 and NumPy. Neither package nor original SolidWorks source files are bundled in this repository. They are not required by the deployed website.
