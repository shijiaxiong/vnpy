# Git Fork 仓库同步与开发标准工作流

> 💡 **核心原则：**
> - `origin` 指向你自己的 Fork 仓库，`upstream` 指向原作者的仓库。
> - 永远不要在本地的 **main 分支**上直接写代码！ 本地的 main 分支仅用于和原仓库（upstream）保持代码同步。
> - 所有的开发工作必须在**新建的分支（Feature Branch）**上进行。

---

## 阶段一：环境初始化（只需配置一次）

克隆你自己的 Fork 仓库后，配置原仓库地址作为代码同步的上游（Upstream）。

```bash
# 1. 查看当前远程仓库配置（此时应该只有 origin）
git remote -v

# 2. 添加原仓库地址，并命名为 upstream
git remote add upstream https://github.com/原作者用户名/原项目名.git

# 3. 再次确认是否添加成功（应有 2个origin, 2个upstream）
git remote -v
```

---

## 阶段二：同步原仓库的最新代码（日常高频操作）

当原仓库（upstream）有代码更新时，按以下步骤将最新代码同步到你的 Fork 仓库中。

```bash
# 1. 切换回主分支
git checkout main

# 2. 获取原仓库的最新更新
git fetch upstream

# 3. 将原仓库的更新合并到本地的 main 分支
git merge upstream/main

# 4. 将同步好的本地 main 分支，推送到你自己的 Fork 仓库
git push origin main
```

执行完毕后，你的 GitHub Fork 仓库即与原项目保持 **100% 一致**。

---

## 阶段三：标准开发工作流（编写你的代码）

保持 main 纯洁，基于最新的 main 创建新分支进行开发。

```bash
# 1. 确保当前在最新 main 分支上
git checkout main

# 2. 创建并切换到功能开发分支（如 feat-login）
git checkout -b feat-login

# 3. 进行日常的开发、修改代码...

# 4. 暂存并提交你的修改
git add .
git commit -m "feat: 添加登录功能"

# 5. 将开发分支推送到你的 Fork 仓库
git push -u origin feat-login
```

推送完成后，前往 GitHub 页面，点击 **Compare & pull request** 提交 PR 即可。

---

## 阶段四：进阶操作 — 开发到一半，原仓库更新了怎么办？

如果你的 feat-login 分支开发了几天，此时原仓库的 main 更新了，为了避免 PR 产生冲突，你需要将原仓库的更新"垫"在你的代码之下（**推荐使用 Rebase**）。

```bash
# 1. 拉取原仓库最新代码
git fetch upstream

# 2. 确保你当前在你的开发分支上
git checkout feat-login

# 3. 将原仓库的最新代码变基（Rebase）到你的分支
git rebase upstream/main

# --- 如果发生冲突（Conflict）---
# a. 手动修改代码文件，解决冲突
# b. 添加解决后的文件：git add .
# c. 继续变基：git rebase --continue
# ------------------------------

# 4. 将变基后的分支强制推送到你的 Fork 仓库
git push -f origin feat-login
```

---

## 附录：异常场景急救 — 先 Clone 了原仓库并改了代码，再去 Fork 怎么办？

如果你一开始直接 clone 了原项目并在本地写了代码（未 commit），此时发现没有推送权限，按以下步骤"狸猫换太子"：

```bash
# 1. 带着未提交的改动，创建并切换到新分支
git checkout -b my-new-feature

# 2. 将原仓库的别名从 origin 改为 upstream
git remote rename origin upstream

# 3. 将你刚刚 Fork 出的仓库地址添加为新的 origin
git remote add origin https://github.com/你的用户名/你的项目名.git

# 4. 提交你的代码
git add .
git commit -m "完成修改"

# 5. 推送到自己的 Fork 仓库
git push -u origin my-new-feature

# 6. 修复本地 main 的追踪关系（收尾）
git checkout main
git branch --set-upstream-to=origin/main main
```
