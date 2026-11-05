# 自动构建与镜像发布

仓库：GitHub Jimme/jx3-equipment-manager，Docker Hub jimmeyoumu/jx3-equipment-manager。

GitHub标准托管Linux runner负责测试和构建，群晖只拉取。自动构建无需连接NAS，不读正式数据库，也不自动重启服务。

## 触发规则

main提交、PR及手动触发运行后端、前端、浏览器测试和Docker构建检查。发布v版本标签时，先通过同样的检查，再推送固定版本和stable。固定版本禁止覆盖，仓库身份、可见性和程序版本都要匹配。

外部PR不登录Docker Hub、不获取发布Secret，不使用pull_request_target运行外部代码。工作流默认contents:read，第三方Action固定到已核验提交SHA。

## 发布配置

仓库Actions Secret：DOCKERHUB_TOKEN，保存具有推送权限的专用访问令牌。令牌不能写进.env、源码、命令参数或镜像。修改工作流和推送标签仍属于可信维护者权限，不向外部贡献者开放发布Secret。

先在代码中统一版本（后端、前端包和页脚），本地验证，再创建v0.1.4这样的版本标签并推送。GitHub运行成功后，NAS按部署说明备份、拉取、重建。

## 本地备用发布

需要Docker引擎。构建使用compose.build.yaml，运行使用compose.yaml，不同时启动两个应用访问同一data。

```sh
docker compose -p jx3-equipment-build -f compose.build.yaml build app
python scripts/publish_hub.py --version 0.1.4 --source-image jx3-equipment-build-app:latest --visibility public
```

发布脚本从隐藏输入接收令牌，临时Docker认证退出即清理。默认仍要求私有仓库，公开仓库必须显式指定--visibility public。已存在的固定版本不能覆盖。

## 免费范围

公开仓库使用标准GitHub托管runner的构建时间免费；仍有执行时长、并发、存储和合理使用限制。不要改用收费larger runner。镜像公开不代表运行实例可以匿名开放公网。
