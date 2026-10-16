# StoUTextureSync 技术审核依据

本文件记录需求设计的技术依据与验证边界；产品范围、功能要求和验收以 [requirements.md](requirements.md) 为准。官方在线文档用于确认接口设计，SP 11.0 与目标 UE 的实际行为由原型验证。

## 导出范围与命名

Painter 导出配置提供 `exportList.filter.uvTiles`，可按 UV 坐标选择 Tile。局部导出的前提是变化事件能完整覆盖受影响区域，包括跨 Tile 绘制和全局图层修改。每个选中 Tile 导出全部启用输出，以覆盖合成法线与打包图的间接依赖。减少导出数量的实际收益由 PoC-02、PoC-04 测量。

`list_project_textures()` 返回按 `(Texture Set 名称, Stack 名称)` 分组的文件列表。Tile 关联需要结合导出配置、Tile 信息、预设模板和预计清单建立。命名按 Texture Set 独立配置，解析结果必须唯一；服装与裸模混合工程中的 Tile 后缀行为由 PoC-03 核对。

导出参数 `sizeLog2` 为单个整数时按长边调整并保持长宽比；参数过滤支持 Texture Set、输出图和 Tile。首版采用 Texture Set 尺寸与部位 Tile 覆盖，最终像素尺寸在预览中展示，并与 SP 11.0 实际输出核对。

依据：[Painter 导出 API](https://experienceleague.adobe.com/en/docs/substance-3d-dev/painter-python/api/substancepainter-package/export)。

## 文件发布与 UE 绑定

完整文件替换能减少半写入暴露，但目录监听是否把替换识别为既有资产修改，取决于实际发布方式和 UE 行为。PoC-01 需要在关闭自动创建与删除的条件下验证监听、源路径匹配和重导入。若路线 A 不能同时保证文件完整与可靠刷新，则用路线 B 协调发布后的定向重导入。

路线 A 的 UE 工程路径用于配置隔离，内容目录用于展示预期资产位置。磁盘上的 `.uasset` 存在不能证明资产类型、导入源关联或当前运行的 UE 工程正确；这些关联由用户在 UE 核对。路线 B 可通过编辑器接口验证并返回结果。

依据：[UE 自动重导入](https://dev.epicgames.com/documentation/en-us/unreal-engine/reimporting-assets-automatically-in-unreal-engine?application_version=5.7)。官方 Unreal Editor 与 Topia Engine 的验证结果分别记录。

## 调度与交互

5 秒端到端目标包含 SP 防抖与 UE 监听等待。原型应采集各阶段耗时、实际导出数量和界面停顿，再调整等待参数。局部、多部位变化及完整同步分别报告，性能结论须注明输出尺寸、场景和宿主版本。

导出调用返回结果后才能处理后续发布；插件通过任务版本拦截失效结果。调用期间 Qt 事件是否响应、进度窗口是否阻塞、原生取消如何返回，以及关闭开关的实际生效时序，需要在 SP 11.0 核对。焦点保持与手绘板连续落笔属于交互验证，不能仅凭主线程调用方式推断其表现。

依据：[Painter 事件 API](https://experienceleague.adobe.com/en/docs/substance-3d-dev/painter-python/api/substancepainter-package/event)、[Painter 导出 API](https://experienceleague.adobe.com/en/docs/substance-3d-dev/painter-python/api/substancepainter-package/export)。

## 工程身份与配置恢复

当前官方工程文档提供 `project.get_uuid()`。采用前应验证 SP 11.0 的可用性，以及重开、移动、另存为和复制后的身份语义。Metadata 可保存插件自定义身份，并随工程保存持久化；工程副本可能保留同一身份，因此身份机制必须配合路径冲突检测与绑定重核对。

未保存工程先保存再建立持久绑定。无法可靠自动识别时，由用户关联本地配置；不能通过路径变化自动推断副本拥有原工程的发布目标。

依据：[Painter 工程 API](https://experienceleague.adobe.com/en/docs/substance-3d-dev/painter-python/api/substancepainter-package/project)。

## 去重、恢复与资源定位

- 去重比较磁盘目标的当前内容与本批输出。重复导出测试同时检查 PNG 元数据稳定性；外部删除、修改或还原目标后应能重新发布。
- 部分发布保留本批完整暂存文件及逐文件结果。继续发布前检查版本有效性；失效或缺失时重新完整同步。
- 写入互斥需覆盖异常退出恢复与活跃持有者保护。锁文件残留、PID 复用和单纯超时都不能直接等同于安全接管。
- 预设以完整资源标识定位。资源失效后由用户重选并核对输出，中文名称、中文路径和跨资源库场景纳入 PoC-03。
