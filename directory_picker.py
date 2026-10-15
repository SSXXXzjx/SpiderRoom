"""使用 Windows 文件资源管理器样式的原生文件夹选择窗口。"""

import ctypes
import json
import sys
import uuid
from ctypes import wintypes
from pathlib import Path


class GUID(ctypes.Structure):
    _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]

    def __init__(self, value):
        super().__init__()
        ctypes.memmove(ctypes.byref(self), uuid.UUID(value).bytes_le, 16)


def com_call(pointer, index, argument_types=(), arguments=()):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argument_types)(table[index])
    return method(pointer, *arguments)


def check_result(result):
    if result < 0:
        raise OSError(f"Windows 目录选择失败（0x{result & 0xffffffff:08X}）")


def pick_directory(initial):
    if sys.platform != "win32":
        raise OSError("目录选择功能需要 Windows")
    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.WinDLL("shell32")
    user32 = ctypes.WinDLL("user32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                                   ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    check_result(ole32.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED
    dialog, folder, selected, filename = (ctypes.c_void_p() for _ in range(4))
    try:
        clsid = GUID("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")  # FileOpenDialog
        iid = GUID("D57C7288-D4AD-4768-BE02-9D969532D960")  # IFileOpenDialog
        item_iid = GUID("43826D1E-E718-42EE-BC55-A1E261C37BFE")  # IShellItem
        check_result(ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(dialog)))
        options = wintypes.DWORD()
        check_result(com_call(dialog, 10, (ctypes.POINTER(wintypes.DWORD),), (ctypes.byref(options),)))
        # FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR
        check_result(com_call(dialog, 9, (wintypes.DWORD,), (options.value | 0x20 | 0x40 | 0x800 | 0x8,)))
        check_result(com_call(dialog, 17, (wintypes.LPCWSTR,), ("选择图片保存目录",)))
        check_result(com_call(dialog, 18, (wintypes.LPCWSTR,), ("选择文件夹",)))

        initial = Path(initial).expanduser()
        while not initial.is_dir() and initial != initial.parent:
            initial = initial.parent
        result = shell32.SHCreateItemFromParsingName(str(initial.resolve()), None, ctypes.byref(item_iid), ctypes.byref(folder))
        if result >= 0:
            check_result(com_call(dialog, 12, (ctypes.c_void_p,), (folder,)))
        result = com_call(dialog, 3, (wintypes.HWND,), (user32.GetForegroundWindow(),))
        if result & 0xffffffff == 0x800704C7:  # 用户取消
            return None
        check_result(result)
        check_result(com_call(dialog, 20, (ctypes.POINTER(ctypes.c_void_p),), (ctypes.byref(selected),)))
        check_result(com_call(selected, 5, (wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)),
                              (0x80058000, ctypes.byref(filename))))  # SIGDN_FILESYSPATH
        return ctypes.wstring_at(filename)
    finally:
        if filename:
            ole32.CoTaskMemFree(filename)
        for pointer in (selected, folder, dialog):
            if pointer:
                com_call(pointer, 2)  # IUnknown.Release
        ole32.CoUninitialize()


def main():
    try:
        initial = sys.argv[1] if len(sys.argv) > 1 else str(Path.home())
        print(json.dumps({"path": pick_directory(initial)}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"error": str(error) or "无法打开 Windows 文件夹选择窗口"}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
