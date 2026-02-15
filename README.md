# Eagle-FUSS: Eagle素材库FUSE文件系统

一个将Eagle素材库映射为普通文件夹结构的FUSE文件系统。

## 功能特性

- 将Eagle素材库（.library目录）映射为普通文件夹结构
- 支持标准的文件系统操作（读、写、创建、删除、重命名等）
- 实时同步Eagle素材库的变化
- 增量更新检查机制（1秒节流）
- 支持文件夹结构变化检测
- 自动生成图片缩略图

## 增量更新检查机制

### 设计原理

系统实现了智能的增量更新检查机制，确保文件系统操作能够感知到Eagle素材库的外部变化：

1. **定时器/节流机制**：在`_EagleLibrarySource`内部实现1秒节流检查
2. **文件变化检测**：通过`mtime.json`时间戳检测素材变化
3. **文件夹变化检测**：通过`metadata.json`的`modificationTime`检测文件夹结构变化
4. **操作前检查**：在每个FUSE文件操作前执行增量更新检查

### 实现细节

- **`_last_check_time`属性**：记录上次检查的时间戳，用于1秒节流
- **`_check_and_update_cache()`方法**：带节流的检查方法，距离上次检查超过1秒才执行更新
- **扩展的`_update_cache()`方法**：同时处理文件变化和文件夹结构变化
- **FUSE操作集成**：在`getattr`、`readdir`、`read`、`write`、`create`、`mkdir`、`rmdir`、`rename`、`truncate`、`unlink`、`utimens`等方法前自动调用检查

### 处理的变化类型

1. **素材被删除**：从缓存映射中移除
2. **素材所属文件夹变更**：更新文件夹映射关系
3. **素材内容变更**：更新文件对象
4. **文件夹结构变化**：重新加载文件夹映射

## 安装与使用

### 依赖安装

```bash
pip install -e .
```

### 运行

```bash
python main.py
```

程序会自动查找当前目录下所有`.library`目录，并将它们挂载为同名目录（去除`.library`后缀）。

## 项目结构

### 模块化设计
项目采用模块化设计，将不同功能拆分到独立的模块中，提高代码的可维护性和可读性。

```
src/
├── __init__.py        # 包导出，保持向后兼容性
├── core.py            # 核心工具函数和常量定义
├── source.py          # 数据源层（EagleLibrarySource类）
├── fuse_operations.py # FUSE操作层（EagleLibrary类）
├── models.py          # 数据模型定义
└── type.py            # 类型别名定义
```

### 模块职责

1. **`src/core.py`** - 核心工具模块
   - `now()`函数：获取当前时间的毫秒时间戳
   - `IMAGE_EXTENSIONS`常量：支持的图片格式集合
   - 其他通用工具函数和常量

2. **`src/source.py`** - 数据源层模块
   - `EagleLibrarySource`类（原`_EagleLibrarySource`，现改为公共类）
   - 与Eagle素材库交互的所有逻辑
   - 缓存管理和增量更新检查机制
   - 文件和文件夹操作方法

3. **`src/fuse_operations.py`** - FUSE操作层模块
   - `EagleLibrary`类：实现FUSE Operations接口
   - 所有FUSE文件系统操作方法
   - 在每个操作前调用增量更新检查

4. **`src/models.py`** - 数据模型模块
   - `File`、`Folder`、`Meta`等数据模型定义
   - 使用`msgspec.Struct`实现高性能序列化

5. **`src/type.py`** - 类型定义模块
   - `ID`、`Stem`等类型别名定义
   - 提高代码类型安全性和可读性

6. **`src/__init__.py`** - 包导出模块
   - 重新导出主要类和函数
   - 保持向后兼容性（导出`_EagleLibrarySource`作为`EagleLibrarySource`的别名）

## 核心组件

### `EagleLibrarySource`类（原`_EagleLibrarySource`）
- 数据源层，负责与Eagle素材库交互
- 维护缓存映射关系（id_map, dir_map, dir_file_map, path_dir_map）
- 实现增量更新检查逻辑（1秒节流）
- 处理文件和文件夹变化检测

### `EagleLibrary`类
- FUSE层，实现FUSE Operations接口
- 将文件系统操作转换为对Eagle素材库的操作
- 在每个操作前调用`self.src._check_and_update_cache()`进行增量更新检查

## 技术细节

- 使用`fusepy`库实现FUSE接口
- 使用`msgspec`进行高性能JSON序列化/反序列化
- 使用`PIL`（Pillow）生成图片缩略图
- 支持单线程模式，无需考虑锁机制

## 注意事项

1. 由于使用FUSE的单线程模式，不需要考虑锁
2. 增量更新检查有1秒节流，避免频繁检查影响性能
3. 需要确保Eagle素材库的`mtime.json`和`metadata.json`文件存在
4. 文件夹结构变化会触发完整的缓存重新初始化
