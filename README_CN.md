# StoUTextureSync

[English](README.md)

StoUTextureSync 是面向 Substance 3D Painter 的插件项目，通过本地导出图片更新
Unreal Engine 中已有的 Texture 资产。

## 当前可用性

项目处于原型阶段。`0.0.0` 只有 Painter 空入口，同步功能和配置面板尚未实现，
当前没有可安装的发布版本。

目标环境为 Windows、Painter 11.0 与官方 Unreal Editor 5.7.4。
M0 原型已在这两个宿主中取得部分证据（[记录](docs/history/m0-host-validation.md)）；
插件本身及正式场景仍待验证。

## 预定使用流程

1. 保存 Painter 工程，选择已安装的导出预设。
2. 设置源贴图目录、UE 工程和预期内容目录，预览 Texture Set 绑定、文件名及尺寸。
3. 手动将图片首次导入 UE，连接现有材质。
4. 为这些源图片配置 UE Auto Reimport，执行手动同步并检查效果。
5. 开启实时同步，在绘制空闲后自动导出。立即同步处理待更新内容，完整同步重新发布全部启用绑定。

输出默认 1K，允许按 Texture Set 或裸模部位显式设置 2K。裸模在 Painter 中使用
Tile 绘制，导出时映射为部位名称，在 UE 中使用独立普通 Texture。服装按各自
Texture Set 命名。自动与手动同步使用相同质量及所选 Painter 预设。

设计目标为最后一次有效修改后五秒内更新；实际延迟及绘制停顿需要原型测量。
各张贴图允许先后更新。源路径变化后需要核对 UE 关联。重开或切换工程时恢复配置，
实时同步默认关闭。

## 开发

可以独立克隆本仓库，也可在合集仓库中初始化对应子模块。代码变更在本仓库的分支
进行，Issues、PR 和发布也由本仓库管理；独立开发不依赖父仓库目录。
修改前阅读 [AGENTS.md](AGENTS.md)；需求如何拆成里程碑、如何评审、宿主验收和归档见
[docs/workflow.md](docs/workflow.md)。

使用 Python 3.10 或更高版本运行骨架检查：

```powershell
python tools/validate_project.py
python -m compileall -q plugins
python -m unittest discover -s prototypes/m0/tests
python -m unittest discover -s tests
```

### 版本与打包

`plugins/stou_texture_sync/__init__.py` 中的 `__version__` 是唯一的产品版本。

```powershell
python tools/version.py              # 显示版本
python tools/version.py set 0.1.0    # 设置版本并发布 CHANGELOG 条目
python tools/package_sp.py           # 生成 dist/StoUTextureSync-<version>-SP.zip
```

`set` 只接受更大的 `MAJOR.MINOR.PATCH` 版本，并把 [CHANGELOG.md](CHANGELOG.md)
中 `Unreleased` 下的条目移到该版本的标题下。归档只含一个 `stou_texture_sync/`
目录，内有运行所需的 Python 文件和 LICENSE，用于解压到 Painter 的
`<Documents>/Adobe/Adobe Substance 3D Painter/python/plugins` 目录。运行目录中
出现其他文件或使用了高于 Painter Python 3.11 的语法时停止打包。从归档安装尚未在
Painter 中验证。

[prototypes/m0](prototypes/m0/README.md) 保存 M0 宿主探针，不随插件安装。

上述检查不代表宿主兼容性已验证。实现和宿主测试由
[M0 技术验证](https://github.com/Alexis-Li/StoUTextureSync/issues/2)与
[M1 首版交付](https://github.com/Alexis-Li/StoUTextureSync/issues/3)管理。
完整范围见[产品需求](https://github.com/Alexis-Li/StoUTextureSync/issues/1)，
术语见 [CONTEXT.md](CONTEXT.md)，集成决策见 [ADR](docs/adr/0001-file-based-sync.md)。

许可证为 [GPL-3.0-only](LICENSE)。
