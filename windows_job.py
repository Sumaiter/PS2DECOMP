"""Windows job ownership for this application's local tool process tree."""
import ctypes
from ctypes import wintypes
import uuid


class BasicLimits(ctypes.Structure):
    _fields_=[('processTime',ctypes.c_longlong),('jobTime',ctypes.c_longlong),
              ('flags',wintypes.DWORD),('minWorkingSet',ctypes.c_size_t),
              ('maxWorkingSet',ctypes.c_size_t),('activeProcesses',wintypes.DWORD),
              ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]


class IOCounters(ctypes.Structure):
    _fields_=[(name,ctypes.c_ulonglong) for name in
              ('readOperations','writeOperations','otherOperations','readBytes','writeBytes','otherBytes')]


class ExtendedLimits(ctypes.Structure):
    _fields_=[('basic',BasicLimits),('io',IOCounters),('processMemory',ctypes.c_size_t),
              ('jobMemory',ctypes.c_size_t),('peakProcessMemory',ctypes.c_size_t),
              ('peakJobMemory',ctypes.c_size_t)]


def kernel():
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];api.CreateJobObjectW.restype=wintypes.HANDLE
    api.OpenJobObjectW.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR];api.OpenJobObjectW.restype=wintypes.HANDLE
    api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    api.GetCurrentProcess.restype=wintypes.HANDLE
    api.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
    api.CloseHandle.argtypes=[wintypes.HANDLE]
    return api


def checked(value):
    if not value:raise ctypes.WinError(ctypes.get_last_error())
    return value


class Job:
    def __init__(self):
        self.api=kernel();self.name='Local\\RAC4Werkbank-'+uuid.uuid4().hex
        self.handle=checked(self.api.CreateJobObjectW(None,self.name))
        try:
            limits=ExtendedLimits();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE
            checked(self.api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)))
        except BaseException:
            self.close();raise

    def terminate(self,code=1):
        checked(self.api.TerminateJobObject(self.handle,code & 0xffffffff))

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle);self.handle=None


def join(name):
    """The host joins before spawning the actual tool; no child can escape the race.

    Keep ownership in the outer controller, so its exit closes the last handle
    and terminates this host and all descendants even if the tool parent exited.
    """
    api=kernel();handle=checked(api.OpenJobObjectW(1,False,name))  # ASSIGN_PROCESS
    try:checked(api.AssignProcessToJobObject(handle,api.GetCurrentProcess()))
    finally:api.CloseHandle(handle)


def finish(name,code):
    api=kernel();handle=checked(api.OpenJobObjectW(8,False,name))  # TERMINATE
    try:checked(api.TerminateJobObject(handle,code & 0xffffffff))
    finally:api.CloseHandle(handle)
