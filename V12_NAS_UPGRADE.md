# V12 NAS 升级说明

此压缩包只包含应用文件，不包含运行数据。升级时保留 NAS 项目目录中的 `config/`，其中存放数据库、密钥和上传的站点 Logo。

## 替换步骤

1. 在群晖 Container Manager 中停止当前项目，或在项目目录执行 `docker compose down`。不要删除项目目录里的 `config/`。
2. 先备份完整的 `config/` 目录。
3. 解压 V12 压缩包，将包内项目文件复制到原项目目录，覆盖同名应用文件；保留原来的 `config/` 目录。
4. 在原项目目录重新构建并启动：

   ```sh
   docker compose up -d --build
   ```

5. 在 Container Manager 检查容器状态；也可以执行 `docker compose ps` 和 `docker compose logs -f app` 查看运行情况。

V12 保留现有数据库格式，不需要清空或重建数据库。不要用新建的空 `config/` 替换旧数据目录，否则会丢失账号、申请和加密配置。
