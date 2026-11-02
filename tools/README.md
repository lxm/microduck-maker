# 工具目录

| 目录 | 功能 | 启动方式 | 是否操作实机 |
|---|---|---|---|
| [calibrator](calibrator/README.md) | 单屏 3D 标定工作台、逐颗保存、回零 / HOME、实时读数与备份 | `cd tools/calibrator && python3 server.py --demo` | 默认模拟；显式使用 SSH 并进入标定后才接入实机 |
| [servo-guide](servo-guide/README.md) | 舵机编号与装配对照 | `python3 -m http.server 8772 --bind 127.0.0.1 --directory tools/servo-guide/site` | 纯查看 |
| [zero-fixture](zero-fixture/README.md) | 零位标定支架：6 件 STL、装配参考和打印说明 | 下载 STL，按毫米、100% 比例交给代打商家 | 实体定位工装，不发送控制命令 |
| [neck-interconnect](neck-interconnect/README.md) | D0.8.1 颈部连接板：原配胶壳短线穿孔焊接、PCB、板级 3D 与打板文件 | 打开 KiCad 工程；按说明试装接线，制造 ZIP 可直接上传 | 实体连接板，未完成实物验收 |

命令从仓库根目录执行。每个工具有自己的 README、来源与许可说明。工具之间不共享实机连接、标定记录或配置。
