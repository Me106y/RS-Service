# -*- coding: utf-8 -*-
"""FlickCD 变化检测模型（源码拷贝自 FlickCD 仓库 model/ 目录）。

仅 decoder.py 的 ``from model.encoder import repvit_m0`` 改为相对导入
``from .encoder import repvit_m0`` 以构成子包；其余代码保持原样。
依赖 timm（SIRIGeo 环境已安装 1.0.30，形状与 best_model.pth 核对一致）。
"""
from .decoder import FlickCD

__all__ = ["FlickCD"]
