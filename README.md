# 剑网三坦克装备管理

本地使用的装备簿：React + TypeScript、FastAPI、SQLite，Docker 单容器部署。依据 [PRD](JX3_Tank_Equipment_Manager_PRD.md) 开发。

## 使用范围

**应用没有登录或访问控制，仅适用于可信局域网。不要端口转发到互联网。公开源码和镜像不代表允许匿名访问你的运行实例。** HTTPS 本身不提供访问权限控制。远程使用需由外部可信访问网关或 VPN 保护。

程序不连接游戏、不采集游戏数据、不预置游戏数值。体质未录入仍可记录装备和使用仓库；未知数值不当作零，不输出看似准确的技能层数。

## 功能

- 游戏账号隔离、角色管理、移除及恢复，12部位多角色装备矩阵。
- 角色表头可勾选 10人、25pt、25yx 副本完成情况。勾选立即保存，下一轮可手动取消。
- 真实品级、体质孔、绿字比较；严格、仅品级、综合三种模式。
- 来源默认可覆盖，装备保留原周期、来源和真实品级快照。
- 周期可勾选「装备均无体质孔」，修改前确认已有装备影响范围。无孔周期装备及戒指跳过缺孔判定，五行石贡献为不适用；比较和计算按装备原周期处理。
- 同账号共享库存、掉落备忘、数量调整、穿戴扣件；旧绑定装备不回库。
- 矩阵按角色、掉落、共享仓库排列。不同配置全部展开；完全相同配置合并数量。点格子直接更正配置，列标题打开数量、穿戴和转入管理；戒指两格共用库存。
- 库存属性可更正全部或部分件数，自动拆分或合并，不改变其他库存及角色穿戴。
- 角色表头「批量录入」支持固定 12 部位、勾选行统一设置、复制通用字段、单行纠错/换装/仓库穿戴/清除；先预览，备份后一次保存，冲突则整批不保存。
- 装备和库存录入保护未保存草稿；附魔不会因更改品级等字段被清空。复制菜单按需加载，默认仅展示仍持有的装备，可勾选包含历史模板。
- 部位精炼与五行石跟角色，附魔及武器五彩石跟具体装备。
- 可逐步补录体质数值，校验完整性及计算口径后显示技能层数。
- JSON导入导出、SQLite一致性备份、恢复预览和冲突保护。

## Docker / 群晖

compose.yaml 与 docker-compose.yml 内容一致。默认镜像 jimmeyoumu/jx3-equipment-manager:stable；固定版本可在.env设置 JX3_IMAGE_TAG。

```sh
docker compose pull app
docker compose up -d --no-build app
```

访问 http://NAS-IP:8088。仅本机使用时，将.env的 JX3_BIND_IP 设为127.0.0.1。数据只挂载 ./data:/app/data，更新时保留整个data及.env。应用准备挂载目录后以非root用户运行，根文件系统只读。没有挂载Docker socket。

详见 [群晖部署](群晖部署.md)、[自动构建与发布](docs/DOCKER-HUB.md)、[公开发布审查](docs/PUBLICATION-REVIEW.md)。

## Windows 本地运行

安装 Python 3.12 和 Node.js 22/24，在项目目录打开PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-Local.ps1 -Install
```

打开 http://127.0.0.1:8000。以后启动不加-Install；修改前端后加-Build。SQLite位于项目data目录。

## 验证

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-Local.ps1 -Browser
```

浏览器测试使用独立.tools目录和8765端口，不使用正式data。包括真实非安全HTTP来源下的录入和仓库流程。

## 备份恢复

网页“数据与备份”生成并下载一致性快照，另存到其他设备。不要在WAL工作期间只复制活动app.sqlite3。在线导入/恢复需预览确认，替换前自动备份。

已有备份可逐份下载、预览恢复或删除。确认删除后会从服务器的 `data/backups` 移除选中的文件。

网页不可用时：停止应用，保留旧data目录，新建data，把已验证快照命名为app.sqlite3放入，不带入旧WAL/SHM，再用匹配程序版本启动核验。不要用Windows程序经网络共享打开NAS活动SQLite。

0.1.10 将数据库升级到迁移 0009：旧周期默认允许体质孔（戒指例外），历史相同配置掉落合并数量并记录审计。旧版 JSON/SQLite 0006–0008 仍可预览导入。结构升级后回退程序必须配对升级前快照，不能直接让旧镜像读取新数据库。

维护者可用 `scripts/verify_update.py before/after --url http://NAS-IP:8088 --state .tools/update-verification.json` 检查升级：前后下载一致性快照并校验完整性、外键、预期迁移和全部业务记录。核验文件包含私人数据，只存本机 `.tools`，验证后按需清理。

## 开发和发布

每轮先更新 PRD 的需求、验收条件及实现状态，再按文档实施。已发布、本地已实现与待实现功能见 PRD 第 16 节。

公开仓库仅包含程序、模板、测试夹具及通用说明；不含真实data、备份、令牌、.env、私人部署记录。发布令牌存放在GitHub Actions Secrets，外部PR只运行测试。标准GitHub托管runner构建镜像，NAS只拉取运行。自动发布不连接或重启用户NAS。
