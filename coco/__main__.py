"""支持 ``python -m coco`` 调用，等价于 ``python -m coco.main``。"""

from coco.main import main

if __name__ == "__main__":
    main()
