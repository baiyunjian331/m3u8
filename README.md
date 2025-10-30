# m3u8

## Usage

1. Install dependencies:

```bash
pip install -r requirements.txt
```

## 将项目推送到 GitHub

如果你希望把当前代码提交并推送到自己的 GitHub 仓库，可以按以下步骤操作：

1. **初始化 Git 仓库（如果尚未初始化）**

   ```bash
   git init
   ```

2. **添加远程仓库地址**（将 `YOUR-USERNAME` 和 `YOUR-REPO` 替换成实际的 GitHub 用户名和仓库名）：

   ```bash
git remote add origin git@github.com:YOUR-USERNAME/YOUR-REPO.git
   ```

   如果使用 HTTPS，可以改为：

   ```bash
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
   ```

3. **提交代码**：

   ```bash
git add .
git commit -m "Initial commit"
   ```

4. **推送到 GitHub**：

   ```bash
git push -u origin main
   ```

首次推送时，Git 会提示你登录或配置访问凭据。完成后，后续只需执行 `git add`、`git commit` 和 `git push` 即可同步到 GitHub。
