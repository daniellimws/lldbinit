'''
.____    .____     ________ __________.__ _______  ._____________
|    |   |    |    \______ \\______   \__|\      \ |__\__    ___/
|    |   |    |     |    |  \|    |  _/  |/   |   \|  | |    |   
|    |___|    |___  |    `   \    |   \  /    |    \  | |    |   
|_______ \_______ \/_______  /______  /__\____|__  /__| |____|   
        \/       \/        \/       \/           \/              LLDBINIT v2.0

A gdbinit clone for LLDB aka how to make LLDB a bit more useful and less crappy

(c) Deroko 2014, 2015, 2016
(c) fG! 2017-2019 - reverser@put.as - https://reverse.put.as
(c) Peternguyen 2020

Available at https://github.com/gdbinit/lldbinit

No original license by Deroko so I guess this is do whatever you want with this
as long you keep original credits and sources references.

Original lldbinit code by Deroko @ https://github.com/deroko/lldbinit
gdbinit available @ https://github.com/gdbinit/Gdbinit

Huge thanks to Deroko for his original effort!

To list all implemented commands use 'lldbinitcmds' command.

How to install it:
------------------

$ cp lldbinit.py ~
$ echo "command script import  ~/lldbinit.py" >>$HOME/.lldbinit

or

$ cp lldbinit.py /Library/Python/2.7/site-packages
$ echo "command script import lldbinit" >> $HOME/.lldbinit

or

just copy it somewhere and use "command script import path_to_script" when you want to load it.

TODO:
-----
- better ARM support and testing - this version is focused on x86/x64
- shortcut to dump memory to file
- check sbthread class: stepoveruntil for example
- help for aliases
- error checking on many return values for lldb objects (target, frame, thread, etc) - verify if frame value is valid on the beginning of each command?
- add source window?
- add threads window?
- remove that ugly stop information (deroko's trick doesn't seem to work anymore, lldb forces that over our captured input?)

- command to search for symbol and display image address (image lookup -s symbol -v) (address is the range)
- command to update breakpoints with new ASLR
- fix get_indirect_flow_target (we can get real load address of the modules - check the new disassembler code)
- solve addresses like lea    rsi, [rip + 0x38cf] (lldb does solve some stuff that it has symbols for and adds the info as comment)
- some sort of colors theme support?

BUGS:
-----

LLDB design:
------------
lldb -> debugger -> target -> process -> thread -> frame(s)
                                      -> thread -> frame(s)
'''
# I mainly use Python 3 as default but when debug XNU Kernel,
# Kernel Debug Kit use Python 2 :|
from __future__ import print_function 

if __name__ == "__main__":
    print("Run only as script from LLDB... Not as standalone program!")

try:
    import  lldb
except:
    pass
import  sys
import  re
import  os
import  time
import  struct
import  argparse
import  subprocess
import  tempfile
from struct import *

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from utils import *
from xnu import *

try:
    from keystone import *
    CONFIG_KEYSTONE_AVAILABLE = 1
except:
    CONFIG_KEYSTONE_AVAILABLE = 0
    pass

VERSION = "2.1"

#
# User configurable options
#
CONFIG_ENABLE_COLOR = 1
CONFIG_DISPLAY_DISASSEMBLY_BYTES = 1
CONFIG_DISASSEMBLY_LINE_COUNT = 8
CONFIG_USE_CUSTOM_DISASSEMBLY_FORMAT = 1
CONFIG_DISPLAY_STACK_WINDOW = 0
CONFIG_DISPLAY_FLOW_WINDOW = 0
CONFIG_NO_CTX = 0
CONFIG_ENABLE_REGISTER_SHORTCUTS = 1
CONFIG_DISPLAY_DATA_WINDOW = 0

# setup the logging level, which is a bitmask of any of the following possible values (don't use spaces, doesn't seem to work)
#
# LOG_VERBOSE LOG_PROCESS LOG_THREAD LOG_EXCEPTIONS LOG_SHLIB LOG_MEMORY LOG_MEMORY_DATA_SHORT LOG_MEMORY_DATA_LONG LOG_MEMORY_PROTECTIONS LOG_BREAKPOINTS LOG_EVENTS LOG_WATCHPOINTS
# LOG_STEP LOG_TASK LOG_ALL LOG_DEFAULT LOG_NONE LOG_RNB_MINIMAL LOG_RNB_MEDIUM LOG_RNB_MAX LOG_RNB_COMM  LOG_RNB_REMOTE LOG_RNB_EVENTS LOG_RNB_PROC LOG_RNB_PACKETS LOG_RNB_ALL LOG_RNB_DEFAULT
# LOG_DARWIN_LOG LOG_RNB_NONE
#
# to see log (at least in macOS)
# $ log stream --process debugserver --style compact
# (or whatever style you like)
CONFIG_LOG_LEVEL = "LOG_NONE"

# removes the offsets and modifies the module name position
# reference: https://lldb.llvm.org/formats.html
CUSTOM_DISASSEMBLY_FORMAT = "\"{${function.initial-function}{${function.name-without-args}} @ {${module.file.basename}}:\n}{${function.changed}\n{${function.name-without-args}} @ {${module.file.basename}}:\n}{${current-pc-arrow} }${addr-file-or-load}: \""
DATA_WINDOW_ADDRESS = 0

# old_x86 = { "eax": 0, "ecx": 0, "edx": 0, "ebx": 0, "esp": 0, "ebp": 0, "esi": 0, "edi": 0, "eip": 0, "eflags": 0,
#       "cs": 0, "ds": 0, "fs": 0, "gs": 0, "ss": 0, "es": 0, }

# old_x64 = { "rax": 0, "rcx": 0, "rdx": 0, "rbx": 0, "rsp": 0, "rbp": 0, "rsi": 0, "rdi": 0, "rip": 0, "rflags": 0,
#       "cs": 0, "fs": 0, "gs": 0, "r8": 0, "r9": 0, "r10": 0, "r11": 0, "r12": 0, 
#       "r13": 0, "r14": 0, "r15": 0 }

# old_arm = { "r0": 0, "r1": 0, "r2": 0, "r3": 0, "r4": 0, "r5": 0, "r6": 0, "r7": 0, "r8": 0, "r9": 0, "r10": 0, 
#           "r11": 0, "r12": 0, "sp": 0, "lr": 0, "pc": 0, "cpsr": 0 }

old_register = {}

arm_type = "thumbv7-apple-ios"

GlobalListOutput = []

Int3Dictionary = {}

crack_cmds = []
crack_cmds_noret = []

All_Registers = [
    "rip", "rax", "rbx", "rbp", "rsp", "rdi", "rsi", "rdx", "rcx", "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15",
    "eip", "eax", "ebx", "ebp", "esp", "edi", "esi", "edx", "ecx"
]

flag_regs = ('rflags', 'eflags', 'cpsr')

segment_regs = ("cs", "ds", "es", "gs", "fs", "ss", "cs", "gs", "fs")

x86_registers = [
    "eax", "ebx", "ebp", "esp", "eflags", "edi", "esi", "edx", "ecx", "eip"
    "cs", "ds", "es", "gs", "fs", "ss"
]

x86_64_registers = [
    "rax", "rbx", "rbp", "rsp", "rflags", "rdi", "rsi", "rdx", "rcx", "rip",
    "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "cs", "gs", "fs"
]

arm_32_registers = [
    "r0", "r1", "r2", "r3", "cpsr", "r4", "r5", "r6", "r7", "r8",
    "r9", "r10", "r11", "r12", "sp", "lr", "pc"
]

aarch64_registers = [
    'x0', 'x1', 'x2', 'x3', 'cpsr',
    'x4', 'x5', 'x6', 'x7', 
    'x8', 'x9', 'x10', 'x11',
    'x12', 'x13', 'x14', 'x15', 
    'x16', 'x17', 'x18', 'x19', 
    'x20', 'x21', 'x22', 'x23', 
    'x24', 'x25', 'x26', 'x27', 
    'x28', 'x29', 'x30', 'sp', 'pc', 'fpcr', 'fpsr'
]

XNU_ZONES = None
SelectedVM = ''

def __lldb_init_module(debugger, internal_dict):
    ''' we can execute commands using debugger.HandleCommand which makes all output to default
    lldb console. With GetCommandinterpreter().HandleCommand() we can consume all output
    with SBCommandReturnObject and parse data before we send it to output (eg. modify it);
    '''

    # don't load if we are in Xcode since it is not compatible and will block Xcode
    if os.getenv('PATH').startswith('/Applications/Xcode'):
        return

    '''
    If I'm running from $HOME where .lldbinit is located, seems like lldb will load 
    .lldbinit 2 times, thus this dirty hack is here to prevent doulbe loading...
    if somebody knows better way, would be great to know :)
    ''' 
    var = debugger.GetInternalVariableValue("stop-disassembly-count", debugger.GetInstanceName())
    if var.IsValid():
        var = var.GetStringAtIndex(0)
        if var == "0":
            return
    
    res = lldb.SBCommandReturnObject()
    ci = debugger.GetCommandInterpreter()

    # settings
    ci.HandleCommand("settings set target.x86-disassembly-flavor intel", res)
    ci.HandleCommand("settings set prompt \"(lldbinit) \"", res)
    #lldb.debugger.GetCommandInterpreter().HandleCommand("settings set prompt \"\033[01;31m(lldb) \033[0m\"", res);
    ci.HandleCommand("settings set stop-disassembly-count 0", res)
    # set the log level - must be done on startup?
    ci.HandleCommand("settings set target.process.extra-startup-command QSetLogging:bitmask=" + CONFIG_LOG_LEVEL + ";", res)
    if CONFIG_USE_CUSTOM_DISASSEMBLY_FORMAT == 1:
        ci.HandleCommand("settings set disassembly-format " + CUSTOM_DISASSEMBLY_FORMAT, res)

    # the hook that makes everything possible :-)
    ci.HandleCommand("command script add -f lldbinit.HandleHookStopOnTarget HandleHookStopOnTarget", res)
    ci.HandleCommand("command script add -f lldbinit.HandleHookStopOnTarget ctx", res)
    ci.HandleCommand("command script add -f lldbinit.HandleHookStopOnTarget context", res)
    # commands
    ci.HandleCommand("command script add -f lldbinit.cmd_lldbinitcmds lldbinitcmds", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_IphoneConnect iphone", res)
    #
    # dump memory commands
    #
    ci.HandleCommand("command script add -f lldbinit.cmd_db db", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_dw dw", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_dd dd", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_dq dq", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_DumpInstructions u", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_findmem findmem", res)
    #
    # ObjectiveC commands
    #
    ci.HandleCommand("command script add -f lldbinit.cmd_objc objc", res)
    #
    # Image commands
    #
    ci.HandleCommand("command script add -f lldbinit.cmd_xinfo xinfo", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_telescope tele", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_vmmap vmmap", res)
    #
    # Exploitation Helper commands
    #
    ci.HandleCommand("command script add -f lldbinit.cmd_pattern_create pattern_create", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_pattern_offset pattern_offset", res)
    #
    # Settings related commands
    #
    ci.HandleCommand("command script add -f lldbinit.cmd_enable enable", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_disable disable", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_contextcodesize contextcodesize", res)
    # a few settings aliases
    ci.HandleCommand("command alias enablesolib enable solib", res)
    ci.HandleCommand("command alias disablesolib disable solib", res)
    ci.HandleCommand("command alias enableaslr enable aslr", res)
    ci.HandleCommand("command alias disableaslr disable aslr", res)
    #
    # Breakpoint related commands
    #
    ci.HandleCommand("command script add -f lldbinit.cmd_m_bp mbp", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_to_ida_addr toida", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_bhb bhb", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_bht bht", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_bpt bpt", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_bpn bpn", res)
    # disable a breakpoint or all
    ci.HandleCommand("command script add -f lldbinit.cmd_bpd bpd", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_bpda bpda", res)
    # clear a breakpoint or all
    ci.HandleCommand("command script add -f lldbinit.cmd_bpc bpc", res)
    ci.HandleCommand("command alias bpca breakpoint delete", res)
    # enable a breakpoint or all
    ci.HandleCommand("command script add -f lldbinit.cmd_bpe bpe", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_bpea bpea", res)
    # commands to set temporary int3 patches and restore original bytes
    ci.HandleCommand("command script add -f lldbinit.cmd_int3 int3", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_rint3 rint3", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_listint3 listint3", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_nop nop", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_null null", res)
    # change eflags commands
    ci.HandleCommand("command script add -f lldbinit.cmd_cfa cfa", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_cfc cfc", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_cfd cfd", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_cfi cfi", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_cfo cfo", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_cfp cfp", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_cfs cfs", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_cft cft", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_cfz cfz", res)
    # skip/step current instruction commands
    ci.HandleCommand("command script add -f lldbinit.cmd_skip skip", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_stepo stepo", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_si si", res)
    # load breakpoints from file
    ci.HandleCommand("command script add -f lldbinit.cmd_LoadBreakPoints lb", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_LoadBreakPointsRva lbrva", res)
    # cracking friends
    ci.HandleCommand("command script add -f lldbinit.cmd_crack crack", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_crackcmd crackcmd", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_crackcmd_noret crackcmd_noret", res)
    # alias for existing breakpoint commands
    # list all breakpoints
    ci.HandleCommand("command alias bpl breakpoint list", res)
    # alias "bp" command that exists in gdbinit - lldb also has alias for "b"
    ci.HandleCommand("command alias bp _regexp-break", res)
    # to set breakpoint commands - I hate typing too much
    ci.HandleCommand("command alias bcmd breakpoint command add", res)
    # launch process and stop at entrypoint (not exactly as gdb command that just inserts breakpoint)
    # usually it will be inside dyld and not the target main()
    ci.HandleCommand("command alias break_entrypoint process launch --stop-at-entry", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_show_loadcmds show_loadcmds", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_show_header show_header", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_tester tester", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_datawin datawin", res)
    # shortcut command to modify registers content
    if CONFIG_ENABLE_REGISTER_SHORTCUTS == 1:
        # x64
        ci.HandleCommand("command script add -f lldbinit.cmd_rip rip", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_rax rax", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_rbx rbx", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_rbp rbp", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_rsp rsp", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_rdi rdi", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_rsi rsi", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_rdx rdx", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_rcx rcx", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_r8 r8", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_r9 r9", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_r10 r10", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_r11 r11", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_r12 r12", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_r13 r13", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_r14 r14", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_r15 r15", res)
        # x86
        ci.HandleCommand("command script add -f lldbinit.cmd_eip eip", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_eax eax", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_ebx ebx", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_ebp ebp", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_esp esp", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_edi edi", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_esi esi", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_edx edx", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_ecx ecx", res)
    if CONFIG_KEYSTONE_AVAILABLE == 1:
        ci.HandleCommand("command script add -f lldbinit.cmd_asm32 asm32", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_asm64 asm64", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_arm32 arm32", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_arm64 arm64", res)
        ci.HandleCommand("command script add -f lldbinit.cmd_armthumb armthumb", res)

    # xnu kernel debug commands
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_showallkexts showallkexts", res)
    # ci.HandleCommand("command script add -f lldbinit.cmd_xnu_breakpoint kbp", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_to_offset ktooff", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_list_all_process showallproc", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_search_process_by_name showproc", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_read_usr_addr readuseraddr", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_set_kdp_pmap setkdp", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_reset_kdp_pmap resetkdp", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_kdp_reboot kdp-reboot", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_show_bootargs showbootargs", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_panic_log panic_log", res)

    # xnu zone commands
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_list_zone zone_list", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_find_zones_by_name zone_find_zones_index", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_zshow_logged_zone zone_show_logged_zone", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_zone_triage zone_triage", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_inspect_zone zone_inspect", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_show_chunk_at zone_show_chunk_at", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_show_chunk_with_regex zone_find_chunk_with_regex", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_find_chunk zone_find_chunk", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_zone_backtrace_at zone_backtrace_at", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xnu_zone_reload zone_reload", res)

    # VMware/Virtualbox support
    ci.HandleCommand("command script add -f lldbinit.cmd_vm_take_snapshot vmsnapshot", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_vm_reverse_snapshot vmrevert", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_vm_delete_snapshot vmdelsnap", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_vm_list_snapshot vmshowsnap", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_vm_show_vm vmlist", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_vm_select_vm vmselect", res)

    ci.HandleCommand("command script add -f lldbinit.cmd_cov cov", res)
    ci.HandleCommand("command script add -f lldbinit.cmd_xu xu", res)

    # add the hook - we don't need to wait for a target to be loaded
    ci.HandleCommand("target stop-hook add -o \"HandleHookStopOnTarget\"", res)
    ci.HandleCommand("command script add --function lldbinit.cmd_banner banner", res)
    debugger.HandleCommand("banner")
    return

def cmd_banner(debugger,command,result,dict):    
    print(COLORS["RED"] + "[+] Loaded lldbinit version: " + VERSION + COLORS["RESET"])

def cmd_lldbinitcmds(debugger, command, result, dict):
    '''Display all available lldbinit commands.'''

    help_table = [
        [ "lldbinitcmds", "this command" ],
        [ "enable", "configure lldb and lldbinit options" ],
        [ "disable", "configure lldb and lldbinit options" ],
        [ "contextcodesize", "set number of instruction lines in code window" ],
        [ "b", "breakpoint address" ],
        [ "bpt", "set a temporary software breakpoint" ],
        [ "bhb", "set an hardware breakpoint" ],
        [ "bpc", "clear breakpoint" ],
        [ "bpca", "clear all breakpoints" ],
        [ "bpd", "disable breakpoint" ],
        [ "bpda", "disable all breakpoints" ],
        [ "bpe", "enable a breakpoint" ],
        [ "bpea", "enable all breakpoints" ],
        [ "bcmd", "alias to breakpoint command add"],
        [ "bpl", "list all breakpoints"],
        [ "bpn", "temporarly breakpoint next instruction" ],
        [ "break_entrypoint", "launch target and stop at entrypoint" ],
        [ "skip", "skip current instruction" ],
        [ "int3", "patch memory address with INT3" ],
        [ "rint3", "restore original byte at address patched with INT3" ],
        [ "listint3", "list all INT3 patched addresses" ],
        [ "nop", "patch memory address with NOP" ],
        [ "null", "patch memory address with NULL" ],
        [ "stepo", "step over calls and loop instructions" ],
        [ "lb", "load breakpoints from file and apply them (currently only func names are applied)" ],
        [ "lbrva", "load breakpoints from file and apply to main executable, only RVA in this case" ],
        [ "db/dw/dd/dq", "memory hex dump in different formats" ],
        [ "findmem", "search memory" ],
        [ "cfa/cfc/cfd/cfi/cfo/cfp/cfs/cft/cfz", "change CPU flags" ],
        [ "u", "dump instructions" ],
        [ "iphone", "connect to debugserver running on iPhone" ],
        [ "ctx/context", "show current instruction pointer CPU context" ],
        [ "show_loadcmds", "show otool output of Mach-O load commands" ],
        [ "show_header", "show otool output of Mach-O header" ],
        [ "enablesolib/disablesolib", "enable/disable the stop on library load events" ],
        [ "enableaslr/disableaslr", "enable/disable process ASLR" ],
        [ "crack", "return from current function" ],
        [ "crackcmd", "set a breakpoint and return from that function" ],
        [ "crackcmd_noret", "set a breakpoint and set a register value. doesn't return from function" ],
        [ "datawin", "set start address to display on data window" ],
        [ "rip/rax/rbx/etc", "shortcuts to modify x64 registers" ],
        [ "eip/eax/ebx/etc", "shortcuts to modify x86 register" ],
        [ "asm32/asm64", "x86/x64 assembler using keystone" ],
        [ "arm32/arm64/armthumb", "ARM assembler using keystone" ],
        [ 'tele', 'view memory page'],
        [ 'xinfo', 'find address belong to image'],
        [ 'pattern_create', 'create cyclic string'],
        [ 'pattern_offset', 'find offset in cyclic string'],
        
        [ 'showallkexts', 'show all loaded kexts (only for xnu kernel debug)'],
        [ 'kbp', 'set breakpoint at offset for specific kext (only for xnu kernel debug)'],
        [ 'ktooff', 'convert current address to offset from basse address of kext (only for xnu kernel debug)'],
        [ 'showallproc', 'show all running process (only for xnu kernel debug)'],
        [ 'showproc', 'show specific process information of target process (only for xnu kernel debug)'],
        [ 'readuseraddr', 'read userspace address (only for xnu kernel debug with kdp-remote)'],
        [ 'setkdp', 'set kdp_pmap (only for xnu kernel debug with kdp-remote)'],
        [ 'resetkdp', 'reset kdp_pmap (only for xnu kernel debug with kdp-remote)'],
        [ 'showbootargs', 'show boot-args of macOS'],
        [ 'kdp-reboot', 'reboot the remote machine'],
        [ 'panic_log', 'show panic log'],
        [ 'zone_list', 'list xnu zones name'],
        [ 'zone_find_zones_index', 'list index of matching zone'],
        [ 'zone_show_logged_zone', 'show all logged zones enable by "-zlog=<zone_name>'],
        [ 'zone_triage', 'detect and print trace log for use after free/double free'],
        [ 'zone_inspect', 'list all chunk in specific zone with their status'],
        [ 'zone_show_chunk_at', 'find chunk address is freed or not'],
        [ 'zone_find_chunk', 'find location of chunk address'],
        [ 'zone_show_chunk_with_regex', 'find location of chunk address by using regex'],
        [ 'zone_backtrace_at', 'list callstack of chunk if btlog is enabled'],
        [ 'zone_reload', 'reload zone if network connection is failed'],

        ['vmsnapshot', 'take snapshot for running virtual machine'],
        ['vmrevert', 'reverse snapshot for running virtual machine'],
        ['vmdelsnap', 'delete snapshot of running virtual machine'],
        ['vmshowsnap', 'show all snapshot of running virtual machine'],
        ['vmlist', 'list running virtual machine'],
        ['vmselect', 'select running virtual machine']
    ]

    print("lldbinit available commands:")

    for row in help_table:
        print(" {: <20} - {: <30}".format(*row))

    print("\nUse \'cmdname help\' for extended command help.")

# placeholder to make tests
def cmd_tester(debugger, command, result, dict):
    print("test")
    #frame = get_frame()
    # the SBValue to ReturnFromFrame must be eValueTypeRegister type
    # if we do a lldb.SBValue() we can't set to that type
    # so we need to make a copy
    # can we use FindRegister() from frame?
    #return_value = frame.reg["rax"]
    #return_value.value = "1"
    #thread.ReturnFromFrame(frame, return_value)


# -------------------------
# Settings related commands
# -------------------------

def cmd_enable(debugger, command, result, dict):
    '''Enable certain lldb and lldbinit options. Use \'enable help\' for more information.'''
    help = """
Enable certain lldb and lldbinit configuration options.

Syntax: enable <setting>

Available settings:
 color: enable color mode.
 solib: enable stop on library events trick.
 aslr: enable process aslr.
 stackwin: enable stack window in context display.
 datawin: enable data window in context display, configure address with datawin.
 flow: call targets and objective-c class/methods.
 """

    global CONFIG_ENABLE_COLOR
    global CONFIG_DISPLAY_STACK_WINDOW
    global CONFIG_DISPLAY_FLOW_WINDOW
    global CONFIG_DISPLAY_DATA_WINDOW
    global CONFIG_NO_CTX

    cmd = command.split()
    if len(cmd) == 0:
        print("[-] error: command requires arguments.")
        print("")
        print(help)
        return

    if cmd[0] == "color":
        CONFIG_ENABLE_COLOR = 1
        print("[+] Enabled color mode.")
    elif cmd[0] == "solib":
        debugger.HandleCommand("settings set target.process.stop-on-sharedlibrary-events true")
        print("[+] Enabled stop on library events trick.")
    elif cmd[0] == "aslr":
        debugger.HandleCommand("settings set target.disable-aslr false")
        print("[+] Enabled ASLR.")
    elif cmd[0] == "stackwin":
        CONFIG_DISPLAY_STACK_WINDOW = 1
        print("[+] Enabled stack window in context display.")
    elif cmd[0] == "flow":
        CONFIG_DISPLAY_FLOW_WINDOW = 1
        print("[+] Enabled indirect control flow window in context display.")
    elif cmd[0] == "datawin":
        CONFIG_DISPLAY_DATA_WINDOW = 1
        print("[+] Enabled data window in context display. Configure address with \'datawin\' cmd.")
    elif cmd[0] == "ctx":
        CONFIG_NO_CTX = 0
        print("[+] Enabled context.")
    elif cmd[0] == "help":
        print(help)
    else:
        print("[-] error: unrecognized command.")
        print(help)

    return

def cmd_disable(debugger, command, result, dict):
    '''Disable certain lldb and lldbinit options. Use \'disable help\' for more information.'''
    help = """
Disable certain lldb and lldbinit configuration options.

Syntax: disable <setting>

Available settings:
 color: disable color mode.
 solib: disable stop on library events trick.
 aslr: disable process aslr.
 stackwin: disable stack window in context display.
 datawin: enable data window in context display.
 flow: call targets and objective-c class/methods.
 """

    global CONFIG_ENABLE_COLOR
    global CONFIG_DISPLAY_STACK_WINDOW
    global CONFIG_DISPLAY_FLOW_WINDOW
    global CONFIG_DISPLAY_DATA_WINDOW
    global CONFIG_NO_CTX

    cmd = command.split()
    if len(cmd) == 0:
        print("[-] error: command requires arguments.")
        print("")
        print(help)
        return

    if cmd[0] == "color":
        CONFIG_ENABLE_COLOR = 0
        print("[+] Disabled color mode.")
    elif cmd[0] == "solib":
        debugger.HandleCommand("settings set target.process.stop-on-sharedlibrary-events false")
        print("[+] Disabled stop on library events trick.")
    elif cmd[0] == "aslr":
        debugger.HandleCommand("settings set target.disable-aslr true")
        print("[+] Disabled ASLR.")
    elif cmd[0] == "stackwin":
        CONFIG_DISPLAY_STACK_WINDOW = 0
        print("[+] Disabled stack window in context display.")
    elif cmd[0] == "flow":
        CONFIG_DISPLAY_FLOW_WINDOW = 0
        print("[+] Disabled indirect control flow window in context display.")
    elif cmd[0] == "datawin":
        CONFIG_DISPLAY_DATA_WINDOW = 0
        print("[+] Disabled data window in context display.")
    elif cmd[0] == "help":
        print(help)
    elif cmd[0] == "ctx":
        CONFIG_NO_CTX = 1
        print("[+] Disabled context.")
    else:
        print("[-] error: unrecognized command.")
        print(help)

    return

def cmd_contextcodesize(debugger, command, result, dict): 
    '''Set the number of disassembly lines in code window. Use \'contextcodesize help\' for more information.'''
    help = """
Configures the number of disassembly lines displayed in code window.

Syntax: contextcodesize <line_count>

Note: expressions supported, do not use spaces between operators.
"""

    global CONFIG_DISASSEMBLY_LINE_COUNT

    cmd = command.split()
    if len(cmd) != 1:
        print("[-] error: please insert the number of disassembly lines to display.")
        print("")
        print(help)
        return
    if cmd[0] == "help":
        print(help)
        print("\nCurrent configuration value is: {:d}".format(CONFIG_DISASSEMBLY_LINE_COUNT))
        return
    
    value = evaluate(cmd[0])
    if value == None:
        print("[-] error: invalid input value.")
        print("")
        print(help)
        return

    CONFIG_DISASSEMBLY_LINE_COUNT = value

    return

# ---------------------------------
# Color and output related commands
# ---------------------------------

def color(x):
    out_col = ""
    if CONFIG_ENABLE_COLOR == 0:
        output(out_col)
        return    
    output(COLORS[x])

# append data to the output that we display at the end of the hook-stop
def output(x):
    global GlobalListOutput
    GlobalListOutput.append(x)


# ---------------------------
# Generate cov
# ---------------------------

def cmd_cov(debugger, command, result, _dict):
    global CONFIG_NO_CTX

    args = command.split(' ')
    if len(args) < 1:
        print('cov <function_name>')
        return

    CONFIG_NO_CTX = 1

    func_name = args[0]
    
    rip = int(str(get_frame().reg["rip"].value), 16)

    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetCommandInterpreter().HandleCommand("bpda", res)
    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetCommandInterpreter().HandleCommand("b " + func_name, res)
    print(res)
    lldb.debugger.GetCommandInterpreter().HandleCommand("c", res)

    rip = int(str(get_frame().reg["rip"].value), 16)
    target_func = resolve_symbol_name(rip)
    print(target_func)

    cur_target = debugger.GetSelectedTarget()
    xinfo = resolve_mem_map(cur_target, rip)
    module_name = xinfo["module_name"]
    module_base = rip - xinfo["abs_offset"]
    print(module_name, hex(module_base))

    with open("cov.txt", "w") as out:
        # out.write(f"{xinfo['module_name']}+0x{xinfo['abs_offset']:x}\n")
        # lldb.debugger.GetCommandInterpreter().HandleCommand("ni", res)
        while True:
            get_process().selected_thread.StepInstruction(False)
            rip = int(str(get_frame().reg["rip"].value), 16)
            xinfo = resolve_mem_map(cur_target, rip)
            out.write(f"{xinfo['module_name']}+0x{xinfo['abs_offset']:x}\n")

            if target_func == resolve_symbol_name(rip) and get_mnemonic(rip) == 'ret':
                get_process().selected_thread.StepInstruction(False)
                break

    CONFIG_NO_CTX = 0

def cmd_xu(debugger, command, result, _dict):
    args = command.split(' ')
    if len(args) < 1:
        print('xu <expression>')
        return
    
    addr = int(get_frame().EvaluateExpression(args[0]).GetValue(), 10)
    error = lldb.SBError()

    ended = False
    s = u''
    offset = 0

    while not ended:
        mem = get_target().GetProcess().ReadMemory(addr + offset, 100, error)
        for i in range(0, 100, 2):
            wc = mem[i+1] << 8 | mem[i]
            s += chr(wc)
            if wc == 0:
                ended = True
                break

        offset += 100

    print(s)

# ---------------------------
# Breakpoint related commands
# ---------------------------

# create breakpoint base on module name and offset
def cmd_m_bp(debugger, command, result, _dict):
    args = command.split(' ')
    if len(args) < 2:
        print('mbp <module_name> <ida default mapped address>')
        return

    module_name = args[0]
    ida_mapped_addr = evaluate(args[1])

    cur_target = debugger.GetSelectedTarget()
    target_module = find_module_by_name(cur_target, module_name)
    if not target_module:
        result.PutCString('Module {0} is not found'.format(module_name))
        return

    text_section = get_text_section(target_module)
    file_base_addr = text_section.file_addr # get default address of module in file
    offset = ida_mapped_addr - file_base_addr

    base_addr = text_section.GetLoadAddress(cur_target) # get ASLR address when module is loaded
    target_addr = base_addr + offset

    cur_target.BreakpointCreateByAddress(target_addr)

    result.PutCString('Done')

def cmd_to_ida_addr(debugger, command, result, _dict):
    args = command.split(' ')
    if len(args) < 2:
        print('toida <module_name> <ida default mapped address>')
        print('Convert lldb ASLR address of specific module to ida mapped address')
        return

    module_name = args[0]
    aslr_mapped_addr = evaluate(args[1])

    cur_target = debugger.GetSelectedTarget()
    target_module = find_module_by_name(cur_target, module_name)
    if not target_module:
        result.PutCString('Module {0} is not found'.format(module_name))
        return
    
    text_section = get_text_section(target_module)

    aslr_base_addr = text_section.GetLoadAddress(cur_target) # get ASLR address when module is loaded
    offset = aslr_mapped_addr - aslr_base_addr
    
    ida_base_addr = text_section.file_addr # get default address of module in file
    ida_mapped_addr = ida_base_addr + offset

    result.PutCString('[+] Ida mapped address of {0} : {1}'.format(module_name, hex(ida_mapped_addr)))

# temporary software breakpoint
def cmd_bpt(debugger, command, result, dict):
    '''Set a temporary software breakpoint. Use \'bpt help\' for more information.'''
    help = """
Set a temporary software breakpoint.

Syntax: bpt <address>

Note: expressions supported, do not use spaces between operators.
"""

    cmd = command.split()
    if len(cmd) != 1:
        print("[-] error: please insert a breakpoint address.")
        print("")
        print(help)
        return
    if cmd[0] == "help":
        print(help)
        return
    
    value = evaluate(cmd[0])
    if not value:
        print("[-] error: invalid input value.")
        print("")
        print(help)
        return
    
    target = get_target()
    breakpoint = target.BreakpointCreateByAddress(value)
    breakpoint.SetOneShot(True)
    breakpoint.SetThreadID(get_frame().GetThread().GetThreadID())

    print("[+] Set temporary breakpoint at 0x{:x}".format(value))
    
# hardware breakpoint
def cmd_bhb(debugger, command, result, dict):
    '''Set an hardware breakpoint'''
    help = """
Set an hardware breakpoint.

Syntax: bhb <address>

Note: expressions supported, do not use spaces between operators.
"""

    cmd = command.split()
    if len(cmd) != 1:
        print("[-] error: please insert a breakpoint address.")
        print("")
        print(help)
        return
    if cmd[0] == "help":
        print(help)
        return
    
    value = evaluate(cmd[0])
    if not value:
        print("[-] error: invalid input value.")
        print("")
        print(help)
        return

    # the python API doesn't seem to support hardware breakpoints
    # so we set it via command line interpreter
    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetCommandInterpreter().HandleCommand("breakpoint set -H -a " + hex(value), res)

    print("[+] Set hardware breakpoint at 0x{:x}".format(value))
    return

# temporary hardware breakpoint
def cmd_bht(debugger, command, result, dict):
    '''Set a temporary hardware breakpoint'''
    print("[-] error: lldb has no x86/x64 temporary hardware breakpoints implementation.")
    return

# clear breakpoint number
def cmd_bpc(debugger, command, result, dict):
    '''Clear a breakpoint. Use \'bpc help\' for more information.'''
    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetCommandInterpreter().HandleCommand("breakpoint delete " + command, res)
    print(res.GetOutput())

# disable breakpoint number
def cmd_bpd(debugger, command, result, dict):
    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetCommandInterpreter().HandleCommand("breakpoint disable " + command, res)
    print(res.GetOutput())

# disable all breakpoints
def cmd_bpda(debugger, command, result, dict):
    '''Disable all breakpoints. Use \'bpda help\' for more information.'''
    help = """
Disable all breakpoints.

Syntax: bpda
"""
        
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
           print(help)
           return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return

    target = get_target()

    if target.DisableAllBreakpoints() == False:
        print("[-] error: failed to disable all breakpoints.")

    print("[+] Disabled all breakpoints.")

# enable breakpoint number
def cmd_bpe(debugger, command, result, dict):
    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetCommandInterpreter().HandleCommand("breakpoint enable " + command, res)
    print(res.GetOutput())

# enable all breakpoints
def cmd_bpea(debugger, command, result, dict):
    '''Enable all breakpoints. Use \'bpea help\' for more information.'''
    help = """
Enable all breakpoints.

Syntax: bpea
"""
        
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
           print(help)
           return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return

    target = get_target()

    if target.EnableAllBreakpoints() == False:
        print("[-] error: failed to enable all breakpoints.")

    print("[+] Enabled all breakpoints.")

# Temporarily breakpoint next instruction - this is useful to skip loops (don't want to use stepo for this purpose)
def cmd_bpn(debugger, command, result, dict):
    '''Temporarily breakpoint instruction at next address. Use \'bpn help\' for more information.'''
    help = """
Temporarily breakpoint instruction at next address

Syntax: bpn

Note: control flow is not respected, it breakpoints next instruction in memory.
"""

    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
           print(help)
           return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return

    target = get_target()
    start_addr = get_current_pc()
    next_addr = start_addr + get_inst_size(start_addr)
    
    breakpoint = target.BreakpointCreateByAddress(next_addr)
    breakpoint.SetOneShot(True)
    breakpoint.SetThreadID(get_frame().GetThread().GetThreadID())

    print("[+] Set temporary breakpoint at 0x{:x}".format(next_addr))

# skip current instruction - just advances PC to next instruction but doesn't execute it
def cmd_skip(debugger, command, result, dict):
    '''Advance PC to instruction at next address. Use \'skip help\' for more information.'''
    help = """
Advance current instruction pointer to next instruction.

Syntax: skip

Note: control flow is not respected, it advances to next instruction in memory.
"""

    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
           print(help)
           return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return

    start_addr = get_current_pc()
    next_addr = start_addr + get_inst_size(start_addr)
    
    if is_x64():
        get_frame().reg["rip"].value = format(next_addr, '#x')
    elif is_i386():
        get_frame().reg["eip"].value = format(next_addr, '#x')
    # show the updated context
    lldb.debugger.HandleCommand("context")

# XXX: ARM breakpoint
def cmd_int3(debugger, command, result, dict):
    '''Patch byte at address to an INT3 (0xCC) instruction. Use \'int3 help\' for more information.'''
    help = """
Patch process memory with an INT3 byte at given address.

Syntax: int3 [<address>]

Note: useful in cases where the debugger breakpoints aren't respected but an INT3 will always trigger the debugger.
Note: ARM not yet supported.
Note: expressions supported, do not use spaces between operators.
"""

    global Int3Dictionary

    error = lldb.SBError()
    target = get_target()

    cmd = command.split()
    # if empty insert a int3 at current PC
    if len(cmd) == 0:
        int3_addr = get_current_pc()
        if int3_addr == 0:
            print("[-] error: invalid current address.")
            return
    elif len(cmd) == 1:
        if cmd[0] == "help":
           print(help)
           return
        
        int3_addr = evaluate(cmd[0])
        if not int3_addr:
            print("[-] error: invalid input address value.")
            print("")
            print(help)
            return
    else:
        print("[-] error: please insert a breakpoint address.")
        print("")
        print(help)
        return

    bytes_string = target.GetProcess().ReadMemory(int3_addr, 1, error)
    if error.Success() == False:
        print("[-] error: Failed to read memory at 0x{:x}.".format(int3_addr))
        return

    bytes_read = bytearray(bytes_string)
    
    patch_bytes = str('\xCC')
    result = target.GetProcess().WriteMemory(int3_addr, patch_bytes, error)
    if error.Success() == False:
        print("[-] error: Failed to write memory at 0x{:x}.".format(int3_addr))
        return

    # save original bytes for later restore
    Int3Dictionary[str(int3_addr)] = bytes_read[0]

    print("[+] Patched INT3 at 0x{:x}".format(int3_addr))
    return

def cmd_rint3(debugger, command, result, dict):
    '''Restore byte at address from a previously patched INT3 (0xCC) instruction. Use \'rint3 help\' for more information.'''
    help = """
Restore the original byte at a previously patched address using \'int3\' command.

Syntax: rint3 [<address>]

Note: expressions supported, do not use spaces between operators.
"""

    global Int3Dictionary

    error = lldb.SBError()
    target = get_target()
    
    cmd = command.split()
    # if empty insert a int3 at current PC
    if len(cmd) == 0:
        int3_addr = get_current_pc()
        if int3_addr == 0:
            print("[-] error: invalid current address.")
            return
    elif len(cmd) == 1:
        if cmd[0] == "help":
           print(help)
           return
        int3_addr = evaluate(cmd[0])
        if not int3_addr:
            print("[-] error: invalid input address value.")
            print("")
            print(help)
            return        
    else:
        print("[-] error: please insert a INT3 patched address.")
        print("")
        print(help)
        return

    if len(Int3Dictionary) == 0:
        print("[-] error: No INT3 patched addresses to restore available.")
        return

    bytes_string = target.GetProcess().ReadMemory(int3_addr, 1, error)
    if error.Success() == False:
        print("[-] error: Failed to read memory at 0x{:x}.".format(int3_addr))
        return
        
    bytes_read = bytearray(bytes_string)

    if bytes_read[0] == 0xCC:
        #print("Found byte patched byte at 0x{:x}".format(int3_addr))
        try:
            original_byte = Int3Dictionary[str(int3_addr)]
        except:
            print("[-] error: Original byte for address 0x{:x} not found.".format(int3_addr))
            return
        patch_bytes = chr(original_byte)
        result = target.GetProcess().WriteMemory(int3_addr, patch_bytes, error)
        if error.Success() == False:
            print("[-] error: Failed to write memory at 0x{:x}.".format(int3_addr))
            return
        # remove element from original bytes list
        del Int3Dictionary[str(int3_addr)]
    else:
        print("[-] error: No INT3 patch found at 0x{:x}.".format(int3_addr))

    return

def cmd_listint3(debugger, command, result, dict):
    '''List all patched INT3 (0xCC) instructions. Use \'listint3 help\' for more information.'''
    help = """
List all addresses patched with \'int3\' command.

Syntax: listint3
"""

    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
           print(help)
           return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return

    if len(Int3Dictionary) == 0:
        print("[-] No INT3 patched addresses available.")
        return

    print("Current INT3 patched addresses:")
    for address, byte in Int3Dictionary.items():
        print("[*] {:s}".format(hex(int(address, 10))))

    return

# XXX: ARM NOPs
def cmd_nop(debugger, command, result, dict):
    '''NOP byte(s) at address. Use \'nop help\' for more information.'''
    help = """
Patch process memory with NOP (0x90) byte(s) at given address.

Syntax: nop <address> [<size>]

Note: default size is one byte if size not specified.
Note: ARM not yet supported.
Note: expressions supported, do not use spaces between operators.
"""

    error = lldb.SBError()
    target = get_target()

    cmd = command.split()
    if len(cmd) == 1:
        if cmd[0] == "help":
           print(help)
           return
        
        nop_addr = evaluate(cmd[0])
        patch_size = 1
        if not nop_addr:
            print("[-] error: invalid address value.")
            print("")
            print(help)
            return
    elif len(cmd) == 2:
        nop_addr = evaluate(cmd[0])
        if not nop_addr:
            print("[-] error: invalid address value.")
            print("")
            print(help)
            return
        
        patch_size = evaluate(cmd[1])
        if not patch_size:
            print("[-] error: invalid size value.")
            print("")
            print(help)
            return
    else:
        print("[-] error: please insert a breakpoint address.")
        print("")
        print(help)
        return

    current_patch_addr = nop_addr
    # format for WriteMemory()
    patch_bytes = str('\x90')
    # can we do better here? WriteMemory takes an input string... weird
    for i in range(patch_size):
        result = target.GetProcess().WriteMemory(current_patch_addr, patch_bytes, error)
        if error.Success() == False:
            print("[-] error: Failed to write memory at 0x{:x}.".format(current_patch_addr))
            return
        current_patch_addr = current_patch_addr + 1

    return

def cmd_null(debugger, command, result, dict):
    '''Patch byte(s) at address to NULL (0x00). Use \'null help\' for more information.'''
    help = """
Patch process memory with NULL (0x00) byte(s) at given address.

Syntax: null <address> [<size>]

Note: default size is one byte if size not specified.
Note: expressions supported, do not use spaces between operators.
"""

    error = lldb.SBError()
    target = get_target()

    cmd = command.split()
    if len(cmd) == 1:
        if cmd[0] == "help":
           print(help)
           return        
        null_addr = evaluate(cmd[0])
        patch_size = 1
        if null_addr == None:
            print("[-] error: invalid address value.")
            print("")
            print(help)
            return
    elif len(cmd) == 2:
        null_addr = evaluate(cmd[0])
        if null_addr == None:
            print("[-] error: invalid address value.")
            print("")
            print(help)
            return
        patch_size = evaluate(cmd[1])
        if patch_size == None:
            print("[-] error: invalid size value.")
            print("")
            print(help)
            return
    else:
        print("[-] error: please insert a breakpoint address.")
        print("")
        print(help)
        return

    current_patch_addr = null_addr
    # format for WriteMemory()
    patch_bytes = str('\x00')
    # can we do better here? WriteMemory takes an input string... weird
    for i in xrange(patch_size):
        result = target.GetProcess().WriteMemory(current_patch_addr, patch_bytes, error)
        if error.Success() == False:
            print("[-] error: Failed to write memory at 0x{:x}.".format(current_patch_addr))
            return
        current_patch_addr = current_patch_addr + 1

    return

'''
    Implements stepover instruction.    
'''
def cmd_stepo(debugger, command, result, dict):
    '''Step over calls and some other instructions so we don't need to step into them. Use \'stepo help\' for more information.'''
    help = """
Step over calls and loops that we want executed but not step into.
Affected instructions: call, movs, stos, cmps, loop.

Syntax: stepo
"""

    cmd = command.split()
    if len(cmd) != 0 and cmd[0] == "help":
        print(help)
        return

    global arm_type
    debugger.SetAsync(True)
    arch = get_arch()
            
    target = get_target()
        
    if is_arm():
        cpsr = get_gp_register("cpsr")
        t = (cpsr >> 5) & 1
        if t:
            #it's thumb
            arm_type = "thumbv7-apple-ios"
        else:
            arm_type = "armv7-apple-ios"

    # compute the next address where to breakpoint
    pc_addr = get_current_pc()
    if pc_addr == 0:
        print("[-] error: invalid current address.")
        return

    next_addr = pc_addr + get_inst_size(pc_addr)
    # much easier to use the mnemonic output instead of disassembling via cmd line and parse
    mnemonic = get_mnemonic(pc_addr)

    if is_arm():
        if "blx" == mnemonic or "bl" == mnemonic:
            breakpoint = target.BreakpointCreateByAddress(next_addr)
            breakpoint.SetThreadID(get_frame().GetThread().GetThreadID())
            breakpoint.SetOneShot(True)
            breakpoint.SetThreadID(get_frame().GetThread().GetThreadID())
            target.GetProcess().Continue()
            return
        else:
            get_process().selected_thread.StepInstruction(False)
            return
    # XXX: make the other instructions besides call user configurable?
    # calls can be call, callq, so use wider matching for those
    if mnemonic == "call" or mnemonic == "callq" or "movs" == mnemonic or "stos" == mnemonic or "loop" == mnemonic or "cmps" == mnemonic:
        breakpoint = target.BreakpointCreateByAddress(next_addr)
        breakpoint.SetOneShot(True)
        breakpoint.SetThreadID(get_frame().GetThread().GetThreadID())
        target.GetProcess().Continue()
    else:
        get_process().selected_thread.StepInstruction(False)

# XXX: help
def cmd_LoadBreakPointsRva(debugger, command, result, dict):
    global  GlobalOutputList
    GlobalOutputList = []
    '''
    frame = get_frame();
        target = lldb.debugger.GetSelectedTarget();

        nummods = target.GetNumModules();
        #for x in range (0, nummods):
        #       mod = target.GetModuleAtIndex(x);
        #       #print(dir(mod));
        #       print(target.GetModuleAtIndex(x));              
        #       for sec in mod.section_iter():
        #               addr = sec.GetLoadAddress(target);
        #               name = sec.GetName();
        #               print(hex(addr));

        #1st module is executable
        mod = target.GetModuleAtIndex(0);
        sec = mod.GetSectionAtIndex(0);
        loadaddr = sec.GetLoadAddress(target);
        if loadaddr == lldb.LLDB_INVALID_ADDRESS:
                sec = mod.GetSectionAtIndex(1);
                loadaddr = sec.GetLoadAddress(target);
        print(hex(loadaddr));
    '''

    target = get_target()
    mod = target.GetModuleAtIndex(0)
    sec = mod.GetSectionAtIndex(0)
    loadaddr = sec.GetLoadAddress(target)
    if loadaddr == lldb.LLDB_INVALID_ADDRESS:
        sec = mod.GetSectionAtIndex(1)
        loadaddr = sec.GetLoadAddress(target)
    try:
        f = open(command, "r")
    except:
        output("[-] Failed to load file : " + command)
        result.PutCString("".join(GlobalListOutput))
        return
    while True:
        line = f.readline()
        if not line: 
            break
        line = line.rstrip()
        if not line: 
            break
        debugger.HandleCommand("breakpoint set -a " + hex(loadaddr + long(line, 16)))
    f.close()


# XXX: help
def cmd_LoadBreakPoints(debugger, command, result, dict):
    global GlobalOutputList
    GlobalOutputList = []

    try:
        f = open(command, "r")
    except:
        output("[-] Failed to load file : " + command)
        result.PutCString("".join(GlobalListOutput))
        return
    while True:
        line = f.readline()
        if not line:
            break
        line = line.rstrip()
        if not line:
            break
        debugger.HandleCommand("breakpoint set --name " + line)
    f.close()

# command that sets rax to 1 or 0 and returns right away from current function
# technically just a shortcut to "thread return"
def cmd_crack(debugger, command, result, dict):
    '''Return from current function and set return value. Use \'crack help\' for more information.'''
    help = """
Return from current function and set return value

Syntax: crack <return value>

Sets rax to return value and returns immediately from current function.
You probably want to use this at the top of the function you want to return from.
"""

    cmd = command.split()
    if len(cmd) != 1:
        print("[-] error: please insert a return value.")
        print("")
        print(help)
        return
    if cmd[0] == "help":
        print(help)
        return

    # breakpoint disable only accepts breakpoint numbers not addresses
    value = evaluate(cmd[0])
    if value == None:
        print("[-] error: invalid return value.")
        print("")
        print(help)
        return

    frame = get_frame()
    # if we copy the SBValue from any register and use that copy
    # for return value we will get that register and rax/eax set
    # on return
    # the SBValue to ReturnFromFrame must be eValueTypeRegister type
    # if we do a lldb.SBValue() we can't set to that type
    # so we need to make a copy
    # can we use FindRegister() from frame?
    return_value = frame.reg["rax"]
    return_value.value = str(value)
    get_thread().ReturnFromFrame(frame, return_value)

# set a breakpoint with return command associated when hit
def cmd_crackcmd(debugger, command, result, dict):
    '''Breakpoint an address, when breakpoint is hit return from function and set return value. Use \'crackcmd help\' for more information.'''
    help = """
Breakpoint an address, when breakpoint is hit return from function and set return value.

Syntax: crackcmd <address> <return value>

Sets rax/eax to return value and returns immediately from current function where breakpoint was set.
"""
    global crack_cmds

    cmd = command.split()
    if len(cmd) == 0:
        print("[-] error: please check required arguments.")
        print("")
        print(help)
        return
    elif len(cmd) > 0 and cmd[0] == "help":
        print(help)
        return
    elif len(cmd) < 2:
        print("[-] error: please check required arguments.")
        print("")
        print(help)
        return        

    # XXX: is there a way to verify if address is valid? or just let lldb error when setting the breakpoint
    address = evaluate(cmd[0])
    if address == None:
        print("[-] error: invalid address value.")
        print("")
        print(help)
        return
    
    return_value = evaluate(cmd[1])
    if return_value == None:
        print("[-] error: invalid return value.")
        print("")
        print(help)
        return
    
    for tmp_entry in crack_cmds:
        if tmp_entry['address'] == address:
            print("[-] error: address already contains a crack command.")
            return

    # set a new entry so we can deal with it in the callback
    new_crack_entry = {}
    new_crack_entry['address'] = address
    new_crack_entry['return_value'] = return_value
    crack_cmds.append(new_crack_entry)

    target = get_target()

    # we want a global breakpoint
    breakpoint = target.BreakpointCreateByAddress(address)
    # when the breakpoint is hit we get this callback executed
    breakpoint.SetScriptCallbackFunction('lldbinit.crackcmd_callback')

def crackcmd_callback(frame, bp_loc, internal_dict):
    global crack_cmds
    # retrieve address we just hit
    current_bp = bp_loc.GetLoadAddress()
    print("[+] warning: hit crack command breakpoint at 0x{:x}".format(current_bp))

    crack_entry = None
    for tmp_entry in crack_cmds:
        if tmp_entry['address'] == current_bp:
            crack_entry = tmp_entry
            break

    if crack_entry == None:
        print("[-] error: current breakpoint not found in list.")
        return

    # we can just set the register in the frame and return empty SBValue
    if is_x64() == True:
        frame.reg["rax"].value = str(crack_entry['return_value']).rstrip('L')
    elif is_i386() == True:
        frame.reg["eax"].value = str(crack_entry['return_value']).rstrip('L')
    else:
        print("[-] error: unsupported architecture.")
        return

    get_thread().ReturnFromFrame(frame, lldb.SBValue())
    get_process().Continue()

# set a breakpoint with a command that doesn't return, just sets the specified register to a value
def cmd_crackcmd_noret(debugger, command, result, dict):
    '''Set a breakpoint and a register to a value when hit. Use \'crackcmd_noret help\' for more information.'''
    help = """
Set a breakpoint and a register to a value when hit.

Syntax: crackcmd_noret <address> <register> <value>

Sets the specified register to a value when the breakpoint at specified address is hit, and resumes execution.
"""
    global crack_cmds_noret

    cmd = command.split()
    if len(cmd) == 0:
        print("[-] error: please check required arguments.")
        print("")
        print(help)
        return
    if len(cmd) > 0 and cmd[0] == "help":
        print(help)
        return
    if len(cmd) < 3:
        print("[-] error: please check required arguments.")
        print("")
        print(help)
        return

    address = evaluate(cmd[0])
    if address == None:
        print("[-] error: invalid address.")
        print("")
        print(help)
        return

    # check if register is set and valid
    if (cmd[1] in All_Registers) == False:
        print("[-] error: invalid register.")
        print("")
        print(help)
        return
    
    value = evaluate(cmd[2])
    if value == None:
        print("[-] error: invalid value.")
        print("")
        print(help)
        return

    register = cmd[1]
    
    for tmp_entry in crack_cmds_noret:
        if tmp_entry['address'] == address:
            print("[-] error: address already contains a crack command.")
            return

    # set a new entry so we can deal with it in the callback
    new_crack_entry = {}
    new_crack_entry['address'] = address
    new_crack_entry['register'] = register
    new_crack_entry['value'] = value
    
    crack_cmds_noret.append(new_crack_entry)

    target = get_target()

    # we want a global breakpoint
    breakpoint = target.BreakpointCreateByAddress(address)
    # when the breakpoint is hit we get this callback executed
    breakpoint.SetScriptCallbackFunction('lldbinit.crackcmd_noret_callback')

def crackcmd_noret_callback(frame, bp_loc, internal_dict):
    global crack_cmds_noret
    # retrieve address we just hit
    current_bp = bp_loc.GetLoadAddress()
    print("[+] warning: hit crack command no ret breakpoint at 0x{:x}".format(current_bp))
    crack_entry = None
    for tmp_entry in crack_cmds_noret:
        if tmp_entry['address'] == current_bp:
            crack_entry = tmp_entry
            break

    if crack_entry == None:
        print("[-] error: current breakpoint not found in list.")
        return

    # must be a string!
    frame.reg[crack_entry['register']].value = str(crack_entry['value']).rstrip('L')
    get_process().Continue()

# -----------------------
# Memory related commands
# -----------------------

'''
    Output nice memory hexdumps...
'''
# display byte values and ASCII characters
def cmd_db(debugger, command, result, dict):
    '''Display hex dump in byte values and ASCII characters. Use \'db help\' for more information.'''
    help = """
Display memory hex dump in byte length and ASCII representation.

Syntax: db [<address>]

Note: if no address specified it will dump current instruction pointer address.
Note: expressions supported, do not use spaces between operators.
"""

    global GlobalListOutput
    GlobalListOutput = []
        
    cmd = command.split()

    if len(cmd) == 0:
        dump_addr = get_current_pc()
        if not dump_addr:
            print("[-] error: invalid current address.")
            return
    elif len(cmd) == 1:
        if cmd[0] == "help":
            print(help)
            return
        dump_addr = evaluate(cmd[0])
        if not dump_addr:
            print("[-] error: invalid input address value.")
            print("")
            print(help)
            return
    else:
        print("[-] error: please insert a start address.")
        print("")
        print(help)
        return

    membuff = try_read_mem(dump_addr, 0x100)
    if not membuff:
        print('[-] error: Your {0} address is not readable'.format(hex(dump_addr)))
        return
    membuff = membuff.ljust(0x100, b'\x00')

    color("BLUE")
    if get_pointer_size() == 4:
        output("[0x0000:0x%.08X]" % dump_addr)
        output("------------------------------------------------------")
    else:
        output("[0x0000:0x%.016lX]" % dump_addr)
        output("------------------------------------------------------")
    color("BOLD")
    output("[data]")
    color("RESET")
    output("\n")
    #output(hexdump(dump_addr, membuff, " ", 16));
    index = 0
    while index < 0x100:
        data = struct.unpack(b"B"*16, membuff[index:index+0x10])
        if get_pointer_size() == 4:
            szaddr = "0x%.08X" % dump_addr
        else:
            szaddr = "0x%.016lX" % dump_addr
        fmtnice = "%.02X %.02X %.02X %.02X %.02X %.02X %.02X %.02X"
        fmtnice = fmtnice + " - " + fmtnice
        output("\033[1m%s :\033[0m %.02X %.02X %.02X %.02X %.02X %.02X %.02X %.02X - %.02X %.02X %.02X %.02X %.02X %.02X %.02X %.02X \033[1m%s\033[0m" % 
            (szaddr, 
            data[0], 
            data[1], 
            data[2], 
            data[3], 
            data[4], 
            data[5], 
            data[6], 
            data[7], 
            data[8], 
            data[9], 
            data[10], 
            data[11], 
            data[12], 
            data[13], 
            data[14], 
            data[15], 
            quotechars(membuff[index:index+0x10])));
        if index + 0x10 != 0x100:
            output("\n")
        index += 0x10
        dump_addr += 0x10
    color("RESET")
    #last element of the list has all data output...
    #so we remove last \n
    result.PutCString("".join(GlobalListOutput))
    result.SetStatus(lldb.eReturnStatusSuccessFinishResult)

# display word values and ASCII characters
def cmd_dw(debugger, command, result, dict):
    ''' Display hex dump in word values and ASCII characters. Use \'dw help\' for more information.'''
    help = """
Display memory hex dump in word length and ASCII representation.

Syntax: dw [<address>]

Note: if no address specified it will dump current instruction pointer address.
Note: expressions supported, do not use spaces between operators.
"""

    global GlobalListOutput
    GlobalListOutput = []

    cmd = command.split()

    if len(cmd) == 0:
        dump_addr = get_current_pc()
        if dump_addr == 0:
            print("[-] error: invalid current address.")
            return
    elif len(cmd) == 1:
        if cmd[0] == "help":
           print(help)
           return
        dump_addr = evaluate(cmd[0])
        if dump_addr == None:
            print("[-] error: invalid input address value.")
            print("")
            print(help)
            return
    else:
        print("[-] error: please insert a start address.")
        print("")
        print(help)
        return

    membuff = try_read_mem(dump_addr, 0x100)
    if not membuff:
        print('[-] error: Your {0} address is not readable'.format(hex(dump_addr)))
        return
    membuff = membuff.ljust(0x100, b'\x00')

    color("BLUE")
    if get_pointer_size() == 4: #is_i386() or is_arm():
        output("[0x0000:0x%.08X]" % dump_addr)
        output("--------------------------------------------")
    else: #is_x64():
        output("[0x0000:0x%.016lX]" % dump_addr)
        output("--------------------------------------------")
    color("BOLD")
    output("[data]")
    color("RESET")
    output("\n")
    index = 0
    while index < 0x100:
        data = struct.unpack("HHHHHHHH", membuff[index:index+0x10])
        if get_pointer_size() == 4:
            szaddr = "0x%.08X" % dump_addr
        else:
            szaddr = "0x%.016lX" % dump_addr
        output("\033[1m%s :\033[0m %.04X %.04X %.04X %.04X %.04X %.04X %.04X %.04X \033[1m%s\033[0m" % (szaddr, 
            data[0],
            data[1],
            data[2],
            data[3],
            data[4],
            data[5],
            data[6],
            data[7],
            quotechars(membuff[index:index+0x10])));
        if index + 0x10 != 0x100:
            output("\n")
        index += 0x10
        dump_addr += 0x10
    color("RESET")
    result.PutCString("".join(GlobalListOutput))
    result.SetStatus(lldb.eReturnStatusSuccessFinishResult)

# display dword values and ASCII characters
def cmd_dd(debugger, command, result, dict):
    ''' Display hex dump in double word values and ASCII characters. Use \'dd help\' for more information.'''
    help = """
Display memory hex dump in double word length and ASCII representation.

Syntax: dd [<address>]

Note: if no address specified it will dump current instruction pointer address.
Note: expressions supported, do not use spaces between operators.
"""

    global GlobalListOutput
    GlobalListOutput = []

    cmd = command.split()

    if len(cmd) == 0:
        dump_addr = get_current_pc()
        if dump_addr == 0:
            print("[-] error: invalid current address.")
            return
    elif len(cmd) == 1:
        if cmd[0] == "help":
           print(help)
           return
        dump_addr = evaluate(cmd[0])
        if not dump_addr:
            print("[-] error: invalid input address value.")
            print("")
            print(help)
            return
    else:
        print("[-] error: please insert a start address.")
        print("")
        print(help)
        return

    membuff = try_read_mem(dump_addr, 0x100)
    if not membuff:
        print('[-] error: Your {0} address is not readable'.format(hex(dump_addr)))
        return
    membuff = membuff.ljust(0x100, b'\x00')

    color("BLUE")
    if get_pointer_size() == 4: #is_i386() or is_arm():
        output("[0x0000:0x%.08X]" % dump_addr)
        output("----------------------------------------")
    else: #is_x64():
        output("[0x0000:0x%.016lX]" % dump_addr)
        output("----------------------------------------")
    color("BOLD")
    output("[data]")
    color("RESET")
    output("\n")
    index = 0
    while index < 0x100:
        (mem0, mem1, mem2, mem3) = struct.unpack("IIII", membuff[index:index+0x10])
        if get_pointer_size() == 4: #is_i386() or is_arm():
            szaddr = "0x%.08X" % dump_addr
        else:  #is_x64():
            szaddr = "0x%.016lX" % dump_addr
        output("\033[1m%s :\033[0m %.08X %.08X %.08X %.08X \033[1m%s\033[0m" % (szaddr, 
                                            mem0, 
                                            mem1, 
                                            mem2, 
                                            mem3, 
                                            quotechars(membuff[index:index+0x10])));
        if index + 0x10 != 0x100:
            output("\n")
        index += 0x10
        dump_addr += 0x10
    color("RESET")
    result.PutCString("".join(GlobalListOutput))
    result.SetStatus(lldb.eReturnStatusSuccessFinishResult)

# display quad values
def cmd_dq(debugger, command, result, dict):
    ''' Display hex dump in quad values. Use \'dq help\' for more information.'''
    help = """
Display memory hex dump in quad word length.

Syntax: dq [<address>]

Note: if no address specified it will dump current instruction pointer address.
Note: expressions supported, do not use spaces between operators.
"""

    global GlobalListOutput
    GlobalListOutput = []

    cmd = command.split()

    if len(cmd) == 0:
        dump_addr = get_current_pc()
        if dump_addr == 0:
            print("[-] error: invalid current address.")
            return
    elif len(cmd) == 1:
        if cmd[0] == "help":
           print(help)
           return        
        dump_addr = evaluate(cmd[0])
        if not dump_addr:
            print("[-] error: invalid input address value.")
            print("")
            print(help)
            return
    else:
        print("[-] error: please insert a start address.")
        print("")
        print(help)
        return

    membuff = try_read_mem(dump_addr, 0x100)
    if not membuff:
        print('[-] error: Your {0} address is not readable'.format(hex(dump_addr)))
        return

    membuff = membuff.ljust(0x100, b'\x00')

    color("BLUE")
    if get_pointer_size() == 4:
        output("[0x0000:0x%.08X]" % dump_addr)
        output("-------------------------------------------------------")
    else:
        output("[0x0000:0x%.016lX]" % dump_addr)
        output("-------------------------------------------------------")
    color("BOLD")
    output("[data]")
    color("RESET")
    output("\n")   
    index = 0
    while index < 0x100:
        (mem0, mem1, mem2, mem3) = struct.unpack("QQQQ", membuff[index:index+0x20])
        if get_pointer_size() == 4:
            szaddr = "0x%.08X" % dump_addr
        else:
            szaddr = "0x%.016lX" % dump_addr
        output("\033[1m%s :\033[0m %.016lX %.016lX %.016lX %.016lX" % (szaddr, mem0, mem1, mem2, mem3))
        if index + 0x20 != 0x100:
            output("\n")
        index += 0x20
        dump_addr += 0x20
    color("RESET")
    result.PutCString("".join(GlobalListOutput))
    result.SetStatus(lldb.eReturnStatusSuccessFinishResult)

# XXX: help
def cmd_findmem(debugger, command, result, dict):
    '''Search memory'''
    help == """
[options]
 -s searches for specified string
 -u searches for specified unicode string
 -b searches binary (eg. -b 4142434445 will find ABCDE anywhere in mem)
 -d searches dword  (eg. -d 0x41414141)
 -q searches qword  (eg. -d 0x4141414141414141)
 -f loads patern from file if it's tooooo big to fit into any of specified options
 -c specify if you want to find N occurances (default is all)
 """

    global GlobalListOutput
    GlobalListOutput = []

    arg = str(command)
    parser = argparse.ArgumentParser(prog="lldb")
    parser.add_argument("-s", "--string",  help="Search string")
    parser.add_argument("-u", "--unicode", help="Search unicode string")
    parser.add_argument("-b", "--binary",  help="Serach binary string")
    parser.add_argument("-d", "--dword",   help="Find dword (native packing)")
    parser.add_argument("-q", "--qword",   help="Find qword (native packing)")
    parser.add_argument("-f", "--file" ,   help="Load find pattern from file")
    parser.add_argument("-c", "--count",   help="How many occurances to find, default is all")

    parser = parser.parse_args(arg.split())
    
    if parser.string != None:
        search_string = parser.string
    elif parser.unicode != None:
        search_string  = unicode(parser.unicode)
    elif parser.binary != None:
        search_string = parser.binary.decode("hex")
    elif parser.dword != None:
        dword = evaluate(parser.dword)
        if not dword:
            print("[-] Error evaluating : " + parser.dword)
            return
        search_string = struct.pack("I", dword & 0xffffffff)
    elif parser.qword != None:
        qword = evaluate(parser.qword)
        if not qword:
            print("[-] Error evaluating : " + parser.qword)
            return
        search_string = struct.pack("Q", qword & 0xffffffffffffffff)
    elif parser.file != None:
        f = 0
        try:
            f = open(parser.file, "rb")
        except:
            print("[-] Failed to open file : " + parser.file)
            return
        search_string = f.read()
        f.close()
    else:
        print("[-] Wrong option... use findmem --help")
        return
    
    count = -1
    if parser.count != None:
        count = evaluate(parser.count)
        if not count:
            print("[-] Error evaluating count : " + parser.count)
            return
    
    process = get_process()
    pid = process.GetProcessID()
    output_data = subprocess.check_output(["/usr/bin/vmmap", "%d" % pid])
    lines = output_data.split("\n")
    #print(lines);
    #this relies on output from /usr/bin/vmmap so code is dependant on that 
    #only reason why it's used is for better description of regions, which is
    #nice to have. If they change vmmap in the future, I'll use my version 
    #and that output is much easier to parse...
    newlines = []
    for x in lines:
        p = re.compile("([\S\s]+)\s([\da-fA-F]{16}-[\da-fA-F]{16}|[\da-fA-F]{8}-[\da-fA-F]{8})")
        m = p.search(x)
        if not m: continue
        tmp = []
        mem_name  = m.group(1)
        mem_range = m.group(2)
        #0x000000-0x000000
        mem_start = long(mem_range.split("-")[0], 16)
        mem_end   = long(mem_range.split("-")[1], 16)
        tmp.append(mem_name)
        tmp.append(mem_start)
        tmp.append(mem_end)
        newlines.append(tmp)
    
    lines = sorted(newlines, key=lambda sortnewlines: sortnewlines[1])
    #move line extraction a bit up, thus we can latter sort it, as vmmap gives
    #readable pages only, and then writable pages, so it looks ugly a bit :)
    newlines = []
    for x in lines:
        mem_name = x[0]
        mem_start= x[1]
        mem_end  = x[2]
        mem_size = mem_end - mem_start
    
        err = lldb.SBError()
                
        membuff = process.ReadMemory(mem_start, mem_size, err)
        if err.Success() == False:
            #output(str(err));
            #result.PutCString("".join(GlobalListOutput));
            continue
        off = 0
        base_displayed = 0

        while True:
            if count == 0: 
                return
            idx = membuff.find(search_string)
            if idx == -1: 
                break
            if count != -1:
                count = count - 1
            off += idx
    
            GlobalListOutput = []
            
            if get_pointer_size() == 4:
                ptrformat = "%.08X"
            else:
                ptrformat = "%.016lX"

            color("RESET")
            output("Found at : ")
            color("GREEN")
            output(ptrformat % (mem_start + off))
            color("RESET")
            if base_displayed == 0:
                output(" base : ")
                color("YELLOW")
                output(ptrformat % mem_start)
                color("RESET")
                base_displayed = 1
            else:
                output("        ")
                if get_pointer_size() == 4:
                    output(" " * 8)
                else:
                    output(" " * 16)
            #well if somebody allocated 4GB of course offset will be to small to fit here
            #but who cares...
            output(" off : %.08X %s" % (off, mem_name))
            print("".join(GlobalListOutput))
            membuff = membuff[idx+len(search_string):]
            off += len(search_string)
    return

def cmd_datawin(debugger, command, result, dict):
    '''Configure address to display in data window. Use \'datawin help\' for more information.'''
    help = """
Configure address to display in data window.

Syntax: datawin <address>

The data window display will be fixed to the address you set. Useful to observe strings being decrypted, etc.
Note: expressions supported, do not use spaces between operators.
"""

    global DATA_WINDOW_ADDRESS

    cmd = command.split()
    if len(cmd) == 0:
        print("[-] error: please insert an address.")
        print("")
        print(help)
        return

    if cmd[0] == "help":
        print(help)
        return        

    dump_addr = evaluate(cmd[0])
    if not dump_addr:
        print("[-] error: invalid address value.")
        print("")
        print(help)
        DATA_WINDOW_ADDRESS = 0
        return
    DATA_WINDOW_ADDRESS = dump_addr

# xinfo command
def cmd_xinfo(debugger, command, result, dict):

    args = command.split(' ')
    if len(args) != 1 or args[0] == '':
        output('Usage : xinfo <address>')
        return

    address = evaluate(args[0])
    if not address:
        print(COLORS['RED'] + 'Invalid address' + COLORS['RESET'])
        return

    cur_target = debugger.GetSelectedTarget()
    xinfo = resolve_mem_map(cur_target, address)
    if not xinfo['module_name']:
        map_info = query_vmmap(address)
        if not map_info:
            print(COLORS['RED'] + 'Your address is not match any image map' + COLORS['RESET'])
            return

        module_name = map_info.type
        offset = address - map_info.start

    else:
        module_name = xinfo['module_name']
        module_name+= '.' + xinfo['section_name']
        offset = xinfo['abs_offset']

    symbol_name = resolve_symbol_name(address)
    print(COLORS['YELLOW'] + '- {0} : {1} ({2})'.format(module_name, hex(offset), symbol_name) + COLORS['RESET'])

def cmd_telescope(debugger, command, result, dict):
    args = command.split(' ')
    print(args)

    if len(args) > 2 or len(args) == 0:
        print('tele/telescope <address / $register> <length (multiply by 8 for x64 and 4 for x86)>')
        return
    
    try:
        address = evaluate(args[0])
        length = evaluate(args[1])
    except IndexError:
        length = 8

    print(COLORS['RED'] + 'CODE' + COLORS['RESET'] + ' | ', end='')
    print(COLORS['YELLOW'] + 'STACK' + COLORS['RESET'] + ' | ', end='')
    print(COLORS['CYAN'] + 'HEAP' + COLORS['RESET'] + ' | ', end='')
    print(COLORS['MAGENTA'] + 'DATA' + COLORS['RESET'])

    cur_target = debugger.GetSelectedTarget()
    process = debugger.GetSelectedTarget().GetProcess()
    pointer_size = get_pointer_size()

    error_ref = lldb.SBError()
    memory = process.ReadMemory(address, length * pointer_size, error_ref)
    if error_ref.Success():
        
        # print telescope memory
        for i in range(length):
            ptr_value = unpack('<Q', memory[i*pointer_size:(i + 1)*pointer_size])[0]

            print('{0}{1}{2}:\t'.format(COLORS['CYAN'], hex(address + i*8), COLORS['RESET']), end='')

            if ptr_value and ((ptr_value >> 48) == 0 or (ptr_value >> 48) == 0xffff):
                xinfo = resolve_mem_map(cur_target, ptr_value)

                offset = xinfo['offset']
                module_name = xinfo['module_name']
                module_name+= '.' + xinfo['section_name']

                if offset > -1:
                    symbol_name = resolve_symbol_name(ptr_value)
                    if xinfo['section_name'] == '__TEXT':
                        # this address is executable
                        color = COLORS['RED']
                    else:
                        color = COLORS['MAGENTA']

                    if symbol_name:
                        print('{0}{1}{2} -> {3}"{4}"{5}'.format(color, hex(ptr_value), COLORS['RESET'], 
                                                                COLORS['BOLD'], symbol_name, COLORS['RESET']))
                    else:
                        print('{0}{1}{2} -> {3}{4}:{5}{6}'.format(
                                color, hex(ptr_value), COLORS['RESET'],
                                COLORS['BOLD'], module_name, hex(xinfo['abs_offset']), COLORS['RESET']
                            ))
                else:
                    error_ref2 = lldb.SBError()
                    process.ReadMemory(ptr_value, 1, error_ref2)

                    if error_ref2.Success():
                        # check this address is on heap or stack or mapped address
                        map_info = query_vmmap(ptr_value)
                        if map_info == None:
                            print('{0}{1}{2}'.format(COLORS['CYAN'], hex(ptr_value), COLORS['RESET']))
                        else:
                            if map_info.type.startswith('Stack'):
                                # is stack address
                                print('{0}{1}{2}'.format(COLORS['YELLOW'], hex(ptr_value), COLORS['RESET']))
                            elif map_info.type.startswith('MALLOC'):
                                # heap
                                print('{0}{1}{2}'.format(COLORS['CYAN'], hex(ptr_value), COLORS['RESET']))
                            else:
                                # mapped address
                                print('{0}{1}{2}'.format(COLORS['MAGENTA'], hex(ptr_value), COLORS['RESET']))
                    else:
                        print(hex(ptr_value))
            else:
                print(hex(ptr_value))

def display_map_info(map_info):
    perm = map_info.perm.split('/')
    if 'x' in perm[0]:
        print(COLORS['RED'], end='')
    elif 'rw' in perm[0]:
        print(COLORS['MAGENTA'], end='')
    elif map_info.type.startswith('Stack'):
        print(COLORS['YELLOW'], end='')
    elif map_info.type.startswith('MALLOC'):
        print(COLORS['CYAN'], end='')
    elif map_info.type.startswith('__TEXT'):
        print(COLORS['RED'], end='')
    elif map_info.type.startswith('__DATA'):
        print(COLORS['MAGENTA'], end='')
    
    print(map_info.type + ' [', end='')

    if get_pointer_size() == 4:
        print("0x%.08X - 0x%.08X" % (map_info.start, map_info.end), end='')
    else:
        print("0x%.016lX - 0x%.016lX" % (map_info.start, map_info.end), end='')

    print(') - ', end='')
    print(map_info.perm, end='')
    print(' {0} {1}'.format(map_info.shm, map_info.region), end='')
    print(COLORS['RESET'])

def cmd_vmmap(debugger, command, result, _dict):
    '''
        vmmap like in Linux
    '''
    if platform.system() == 'Linux':
        # cat /proc/pid/maps
        proc = get_process()
        proc_id = proc.GetProcessID()

        with open('/proc/{0}/maps'.format(proc_id), 'r') as f:
            map_info = f.read()
            print(map_info)

        return

    if platform.system() != 'Darwin':
        print('[!] This command only works in macOS')
        return

    addr = evaluate(command)
    if not addr:
        # add color or sth like in this text
        map_infos = parse_vmmap_info()

        for map_info in map_infos:
            display_map_info(map_info)

        return

    map_info = query_vmmap(addr)
    if not map_info:
        print('[-] Unable to find your address {0}'.format(hex(addr)))
        return

    display_map_info(map_info)

def cmd_objc(debugger, command, result, _dict):
    '''
        Return class name of objectiveC object
    '''
    
    objc_addr = evaluate(command)
    if not objc_addr:
        print('objc <register/address> => return class name of objectiveC object')
        return
    
    class_name = objc_get_classname(hex(objc_addr))
    # print content or structure of this objc object
    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetCommandInterpreter().HandleCommand('p *(({0} *){1})'.format(class_name, hex(objc_addr)), res)
    if res.Succeeded():
        print(res.GetOutput())

def cmd_pattern_create(debugger, command, result, _dict):
    pattern_length = parse_number(command) 

    if pattern_length <= 0:
        print('Invalid pattern_length')
        return

    print(cyclic(pattern_length).decode('utf-8'))

def cmd_pattern_offset(debugger, command, result, _dict):
    args = command.split(' ')

    if len(args) != 2:
        print('pattern_offset <value / $register> <length (multiply by 8 for x64 and 4 for x86)>')
        return

    value = evaluate(args[0])
    if value == 0:
        print(f'Your value "{args[0]}" is invalid')
        return

    length = parse_number(args[1])

    pos = cyclic_find(value, length)
    print('Value {0}{1}{2} locate at offset {3}{4}{5}'.format(
        COLORS['YELLOW'], hex(value), COLORS['RESET'], COLORS['YELLOW'], hex(pos), COLORS['RESET'])
    )

# shortcut functions to modify each register
def cmd_rip(debugger, command, result, dict):
    update_register("rip", command)

def cmd_rax(debugger, command, result, dict):
    update_register("rax", command)

def cmd_rbx(debugger, command, result, dict):
    update_register("rbx", command)

def cmd_rbp(debugger, command, result, dict):
    update_register("rbp", command)

def cmd_rsp(debugger, command, result, dict):
    update_register("rsp", command)

def cmd_rdi(debugger, command, result, dict):
    update_register("rdi", command)

def cmd_rsi(debugger, command, result, dict):
    update_register("rsi", command)

def cmd_rdx(debugger, command, result, dict):
    update_register("rdx", command)

def cmd_rcx(debugger, command, result, dict):
    update_register("rcx", command)

def cmd_r8(debugger, command, result, dict):
    update_register("r8", command)

def cmd_r9(debugger, command, result, dict):
    update_register("r9", command)

def cmd_r10(debugger, command, result, dict):
    update_register("r10", command)

def cmd_r11(debugger, command, result, dict):
    update_register("r11", command)

def cmd_r12(debugger, command, result, dict):
    update_register("r12", command)

def cmd_r13(debugger, command, result, dict):
    update_register("r13", command)

def cmd_r14(debugger, command, result, dict):
    update_register("r14", command)

def cmd_r15(debugger, command, result, dict):
    update_register("r15", command)

def cmd_eip(debugger, command, result, dict):
    update_register("eip", command)

def cmd_eax(debugger, command, result, dict):
    update_register("eax", command)

def cmd_ebx(debugger, command, result, dict):
    update_register("ebx", command)

def cmd_ebp(debugger, command, result, dict):
    update_register("ebp", command)

def cmd_esp(debugger, command, result, dict):
    update_register("esp", command)

def cmd_edi(debugger, command, result, dict):
    update_register("edi", command)

def cmd_esi(debugger, command, result, dict):
    update_register("esi", command)

def cmd_edx(debugger, command, result, dict):
    update_register("edx", command)

def cmd_ecx(debugger, command, result, dict):
    update_register("ecx", command)

# -----------------------------
# modify eflags/rflags commands
# -----------------------------

def modify_eflags(flag):
    # read the current value so we can modify it
    if is_x64():
        eflags = get_gp_register("rflags")
    elif is_i386():
        eflags = get_gp_register("eflags")
    else:
        print("[-] error: unsupported architecture.")
        return

    masks = { "CF":0, "PF":2, "AF":4, "ZF":6, "SF":7, "TF":8, "IF":9, "DF":10, "OF":11 }
    if flag not in masks.keys():
        print("[-] error: requested flag not available")
        return
    # we invert whatever value is set
    if bool(eflags & (1 << masks[flag])) == True:
        eflags = eflags & ~(1 << masks[flag])
    else:
        eflags = eflags | (1 << masks[flag])

    # finally update the value
    if is_x64():
        get_frame().reg["rflags"].value = format(eflags, '#x')
    elif is_i386():
        get_frame().reg["eflags"].value = format(eflags, '#x')

def cmd_cfa(debugger, command, result, dict):
    '''Change adjust flag. Use \'cfa help\' for more information.'''
    help = """
Flip current adjust flag.

Syntax: cfa
"""
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
            print(help)
            return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return
    modify_eflags("AF")

def cmd_cfc(debugger, command, result, dict):
    '''Change carry flag. Use \'cfc help\' for more information.'''
    help = """
Flip current carry flag.

Syntax: cfc
"""
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
            print(help)
            return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return
    modify_eflags("CF")

def cmd_cfd(debugger, command, result, dict):
    '''Change direction flag. Use \'cfd help\' for more information.'''
    help = """
Flip current direction flag.

Syntax: cfd
"""
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
            print(help)
            return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return
    modify_eflags("DF")

def cmd_cfi(debugger, command, result, dict):
    '''Change interrupt flag. Use \'cfi help\' for more information.'''
    help = """
Flip current interrupt flag.

Syntax: cfi
"""
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
            print(help)
            return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return
    modify_eflags("IF")

def cmd_cfo(debugger, command, result, dict):
    '''Change overflow flag. Use \'cfo help\' for more information.'''
    help = """
Flip current overflow flag.

Syntax: cfo
"""
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
            print(help)
            return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return
    modify_eflags("OF")

def cmd_cfp(debugger, command, result, dict):
    '''Change parity flag. Use \'cfp help\' for more information.'''
    help = """
Flip current parity flag.

Syntax: cfp
"""
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
            print(help)
            return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return
    modify_eflags("PF")

def cmd_cfs(debugger, command, result, dict):
    '''Change sign flag. Use \'cfs help\' for more information.'''
    help = """
Flip current sign flag.

Syntax: cfs
"""
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
            print(help)
            return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return
    modify_eflags("SF")

def cmd_cft(debugger, command, result, dict):
    '''Change trap flag. Use \'cft help\' for more information.'''
    help = """
Flip current trap flag.

Syntax: cft
"""
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
            print(help)
            return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return
    modify_eflags("TF")

def cmd_cfz(debugger, command, result, dict):
    '''Change zero flag. Use \'cfz help\' for more information.'''
    help = """
Flip current zero flag.

Syntax: cfz
""" 
    cmd = command.split()
    if len(cmd) != 0:
        if cmd[0] == "help":
            print(help)
            return
        print("[-] error: command doesn't take any arguments.")
        print("")
        print(help)
        return
    modify_eflags("ZF")

'''
    si, c, r instruction override deault ones to consume their output.
    For example:
        si is thread step-in which by default dumps thread and frame info
        after every step. Consuming output of this instruction allows us
        to nicely display informations in our hook-stop
    Same goes for c and r (continue and run)
'''
def cmd_si(debugger, command, result, dict):
    debugger.SetAsync(True)
    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetSelectedTarget().process.selected_thread.StepInstruction(False)
    result.SetStatus(lldb.eReturnStatusSuccessFinishNoResult)

def c(debugger, command, result, dict):
    debugger.SetAsync(True)
    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetSelectedTarget().GetProcess().Continue()
    result.SetStatus(lldb.eReturnStatusSuccessFinishNoResult)

# ------------------------------
# Disassembler related functions
# ------------------------------

'''
    Handles 'u' command which displays instructions. Also handles output of
    'disassemble' command ...
'''
# XXX: help
def cmd_DumpInstructions(debugger, command, result, dict):
    '''Dump instructions at certain address (SoftICE like u command style)'''
    help = """ """

    global GlobalListOutput
    GlobalListOutput = []
    
    target = get_target()
    cmd = command.split()
    if len(cmd) == 0 or len(cmd) > 2:
        disassemble(get_current_pc(), CONFIG_DISASSEMBLY_LINE_COUNT)
    elif len(cmd) == 1:
        address = evaluate(cmd[0])
        if not address:
            return
        disassemble(address, CONFIG_DISASSEMBLY_LINE_COUNT)
    else:
        address = evaluate(cmd[0])
        if not address:
            return
        count = evaluate(cmd[1])
        if not count:
            return
        disassemble(address, count)

    result.PutCString("".join(GlobalListOutput))
    result.SetStatus(lldb.eReturnStatusSuccessFinishResult)

# return the instruction mnemonic at input address
def get_mnemonic(target_addr):
    err = lldb.SBError()
    target = get_target()

    instruction_list = target.ReadInstructions(lldb.SBAddress(target_addr, target), 1, 'intel')
    if instruction_list.GetSize() == 0:
        print("[-] error: not enough instructions disassembled.")
        return ""

    cur_instruction = instruction_list.GetInstructionAtIndex(0)
    # much easier to use the mnemonic output instead of disassembling via cmd line and parse
    mnemonic = cur_instruction.GetMnemonic(target)

    return mnemonic

# returns the instruction operands
def get_operands(source_address):
    err = lldb.SBError()
    target = get_target()
    # use current memory address
    # needs to be this way to workaround SBAddress init bug
    # src_sbaddr = lldb.SBAddress()
    # src_sbaddr.load_addr = source_address
    src_sbaddr = lldb.SBAddress(source_address, target)
    instruction_list = target.ReadInstructions(src_sbaddr, 1, 'intel')
    if instruction_list.GetSize() == 0:
        print("[-] error: not enough instructions disassembled.")
        return ""    
    cur_instruction = instruction_list[0]
    # return cur_instruction.operands
    return cur_instruction.GetOperands(target)

# find out the size of an instruction using internal disassembler
def get_inst_size(target_addr):
    target = get_target()

    instruction_list = target.ReadInstructions(lldb.SBAddress(target_addr, target), 1, 'intel')
    if instruction_list.GetSize() == 0:
        print("[-] error: not enough instructions disassembled.")
        return 0

    cur_instruction = instruction_list.GetInstructionAtIndex(0)
    return cur_instruction.size

# the disassembler we use on stop context
# we can customize output here instead of using the cmdline as before and grabbing its output
def disassemble(start_address, count):
    target = get_target()
    if target == None:
        return
    # this init will set a file_addr instead of expected load_addr
    # and so the disassembler output will be referenced to the file address
    # instead of the current loaded memory address
    # this is annoying because all RIP references will be related to file addresses
    file_sbaddr = lldb.SBAddress(start_address, target)
    # create a SBAddress object with the load_addr set so we can disassemble with
    # current memory addresses and what is happening right now
    # we use the empty init and then set the property which is read/write for load_addr
    # this whole thing seems like a bug?
    # mem_sbaddr = lldb.SBAddress()
    # mem_sbaddr.load_addr = start_address
    # disassemble to get the file and memory version
    # we could compute this by finding sections etc but this way it seems
    # much simpler and faster
    # this seems to be a bug or missing feature because there is no way
    # to distinguish between the load and file addresses in the disassembler
    # the reason might be because we can't create a SBAddress that has
    # load_addr and file_addr set so that the disassembler can distinguish them
    # somehow when we use file_sbaddr object the SBAddress GetLoadAddress()
    # retrieves the correct memory address for the instruction while the
    # SBAddress GetFileAddress() retrives the correct file address
    # but the branch instructions addresses are the file addresses
    # bug on SBAddress init implementation???
    # this also has problems with symbols - the memory version doesn't have them
    # instructions_mem = target.ReadInstructions(mem_sbaddr, count, "intel")
    instructions_file = target.ReadInstructions(file_sbaddr, count, "intel")
    # if instructions_mem.GetSize() != instructions_file.GetSize():
    #   print("[-] error: instructions arrays sizes are different.")
    #   return
    # find out the biggest instruction lenght and mnemonic length
    # so we can have a uniform output
    max_size = 0
    max_mnem_size = 0
    # for i in instructions_mem:
    #   if i.size > max_size:
    #       max_size = i.size
    #   mnem_len = len(i.mnemonic)
    #   if mnem_len > max_mnem_size:
    #       max_mnem_size = mnem_len
    for instr in instructions_file:
        if instr.size > max_size:
            max_size = instr.size

        mnem_len = len(instr.GetMnemonic(target))
        if mnem_len > max_mnem_size:
            max_mnem_size = mnem_len
    
    current_pc = get_current_pc()
    # get info about module if there is a symbol
    module = file_sbaddr.module
    #module_name = module.file.GetFilename()
    module_name = module.file.fullpath

    count = 0
    blockstart_sbaddr = None
    blockend_sbaddr = None
    # for mem_inst in instructions_mem:
    for mem_inst in instructions_file:
        # get the same instruction but from the file version because we need some info from it
        file_inst = instructions_file[count]
        # try to extract the symbol name from this location if it exists
        # needs to be referenced to file because memory it doesn't work
        symbol_name = instructions_file[count].addr.GetSymbol().GetName()
        # if there is no symbol just display module where current instruction is
        # also get rid of unnamed symbols since they are useless
        if not symbol_name or "___lldb_unnamed_symbol" in symbol_name:
            if count == 0:
                if CONFIG_ENABLE_COLOR == 1:
                    color(COLOR_SYMBOL_NAME)
                    output("@ {}:".format(module_name) + "\n")
                    color("RESET")
                else:
                    output("@ {}:".format(module_name) + "\n")            
        elif symbol_name:
            # print the first time there is a symbol name and save its interval
            # so we don't print again until there is a different symbol
            cur_load_addr = file_inst.GetAddress().GetLoadAddress(target)

            blockstart_addr = 0
            if blockstart_sbaddr:
                blockstart_addr = blockstart_sbaddr.GetLoadAddress(target)

            blockend_addr = 0
            if blockend_sbaddr:
                blockend_addr = blockend_sbaddr.GetLoadAddress(target)

            # if not blockstart_sbaddr or (int(file_inst.addr) < int(blockstart_sbaddr)) or (int(file_inst.addr) >= int(blockend_sbaddr)):
            if not blockstart_addr or (cur_load_addr < blockstart_addr) \
                                                                or (cur_load_addr >= blockend_addr):
                if CONFIG_ENABLE_COLOR == 1:
                    color(COLOR_SYMBOL_NAME)
                    output("{} @ {}:".format(symbol_name, module_name) + "\n")
                    color("RESET")
                else:
                    output("{} @ {}:".format(symbol_name, module_name) + "\n")
                blockstart_sbaddr = file_inst.addr.GetSymbol().GetStartAddress()
                blockend_sbaddr = file_inst.addr.GetSymbol().GetEndAddress()
        
        # get the instruction bytes formatted as uint8
        inst_data = mem_inst.GetData(target).uint8
        # mnem = mem_inst.mnemonic
        mnem = mem_inst.GetMnemonic(target)
        # operands = mem_inst.operands
        operands = mem_inst.GetOperands(target)
        bytes_string = ""
        total_fill = max_size - mem_inst.size
        total_spaces = mem_inst.size - 1
        for x in inst_data:
            bytes_string += "{:02x}".format(x)
            if total_spaces > 0:
                bytes_string += " "
                total_spaces -= 1
        if total_fill > 0:
            # we need one more space because the last byte doesn't have space
            # and if we are smaller than max size we are one space short
            bytes_string += "  " * total_fill
            bytes_string += " " * total_fill
        
        mnem_len = len(mem_inst.GetMnemonic(target))
        if mnem_len < max_mnem_size:
            missing_spaces = max_mnem_size - mnem_len
            mnem += " " * missing_spaces

        # the address the current instruction is loaded at
        # we need to extract the address of the instruction and then find its loaded address
        memory_addr = mem_inst.addr.GetLoadAddress(target)
        # the address of the instruction in the current module
        # for main exe it will be the address before ASLR if enabled, otherwise the same as current
        # for modules it will be the address in the module code, not the address it's loaded at
        # so we can use this address to quickly get to current instruction in module loaded at a disassembler
        # without having to rebase everything etc
        #file_addr = mem_inst.addr.GetFileAddress()
        file_addr = file_inst.addr.GetFileAddress()

        # fix dyld_shared_arm64 dispatch function to correct symbol name
        dyld_resolve_name = ''
        dyld_call_addr = 0
        if is_aarch64() and instructions_file[count].GetMnemonic(target) in ('bl', 'b'):
            indirect_addr = get_indirect_flow_target(memory_addr)
            dyld_call_addr = dyld_arm64_resolve_dispatch(target, indirect_addr)
            dyld_resolve_name = resolve_symbol_name(dyld_call_addr)
        
        # comment = ""
        # if file_inst.comment != "":
        #   comment = " ; " + file_inst.comment
        if not dyld_resolve_name:
            comment = file_inst.GetComment(target)
            if comment != '':
                comment = " ; " + comment
        else:
            comment = " ; resolve symbol stub: j___" + dyld_resolve_name

        if current_pc == memory_addr:
            # try to retrieve extra information if it's a branch instruction
            # used to resolve indirect branches and try to extract Objective-C selectors
            if mem_inst.DoesBranch():

                if dyld_call_addr:
                    flow_addr = dyld_call_addr
                else:
                    flow_addr = get_indirect_flow_address(mem_inst.GetAddress().GetLoadAddress(target))
                    
                if flow_addr > 0:
                    flow_module_name = get_module_name(flow_addr)
                    symbol_info = ""
                    # try to solve the symbol for the target address
                    # target_symbol_name = lldb.SBAddress(flow_addr,target).GetSymbol().GetName()
                    target_symbol_name = resolve_symbol_name(flow_addr)
                    # if there is a symbol append to the string otherwise
                    # it will be empty and have no impact in output
                    if target_symbol_name:
                        symbol_info = target_symbol_name + " @ "
                    
                    if comment == "":
                        # remove space for instructions without operands
                        # if mem_inst.operands == "":
                        if mem_inst.GetOperands(target):
                            comment = "; " + symbol_info + hex(flow_addr) + " @ " + flow_module_name
                        else:
                            comment = " ; " + symbol_info + hex(flow_addr) + " @ " + flow_module_name
                    else:
                        comment = comment + " " + hex(flow_addr) + " @ " + flow_module_name
                
                objc = ''
                if dyld_call_addr:
                    objc = get_objectivec_selector_at(dyld_call_addr)
                else:
                    objc = get_objectivec_selector(current_pc)
                
                if objc != "":
                    comment = comment + " -> " + objc

            if CONFIG_ENABLE_COLOR == 1:
                color("BOLD")
                color(COLOR_CURRENT_PC)
                output("->  0x{:x} (0x{:x}): {}  {}   {}{}".format(memory_addr, file_addr, bytes_string, mnem, operands, comment) + "\n")
                color("RESET")
            else:
                output("->  0x{:x} (0x{:x}): {}  {}   {}{}".format(memory_addr, file_addr, bytes_string, mnem, operands, comment) + "\n")
        else:
            output("    0x{:x} (0x{:x}): {}  {}   {}{}".format(memory_addr, file_addr, bytes_string, mnem, operands, comment) + "\n")

        count += 1
    
    return

# ------------------------------------
# Commands that use external utilities
# ------------------------------------

def cmd_show_loadcmds(debugger, command, result, dict): 
    '''Show otool output of Mach-O load commands. Use \'show_loadcmds\' for more information.'''
    help = """
Show otool output of Mach-O load commands.

Syntax: show_loadcmds <address>

Where address is start of Mach-O header in memory.
Note: expressions supported, do not use spaces between operators.
"""
    
    error = lldb.SBError()

    cmd = command.split()
    if len(cmd) == 1:
        if cmd[0] == "help":
           print(help)
           return
        header_addr = evaluate(cmd[0])
        if not header_addr:
            print("[-] error: invalid header address value.")
            print("")
            print(help)
            return        
    else:
        print("[-] error: please insert a valid Mach-O header address.")
        print("")
        print(help)
        return

    if os.path.isfile("/usr/bin/otool") == False:
            print("/usr/bin/otool not found. Please install Xcode or Xcode command line tools.")
            return
    
    bytes_string = get_process().ReadMemory(header_addr, 4096*10, error)
    if error.Success() == False:
        print("[-] error: Failed to read memory at 0x{:x}.".format(header_addr))
        return

    # open a temporary filename and set it to delete on close
    f = tempfile.NamedTemporaryFile(delete=True)
    f.write(bytes_string)
    # pass output to otool
    output_data = subprocess.check_output(["/usr/bin/otool", "-l", f.name])
    # show the data
    print(output_data)
    # close file - it will be automatically deleted
    f.close()

    return

def cmd_show_header(debugger, command, result, dict): 
    '''Show otool output of Mach-O header. Use \'show_header\' for more information.'''
    help = """
Show otool output of Mach-O header.

Syntax: show_header <address>

Where address is start of Mach-O header in memory.
Note: expressions supported, do not use spaces between operators.
"""

    error = lldb.SBError()

    cmd = command.split()
    if len(cmd) == 1:
        if cmd[0] == "help":
           print(help)
           return
        header_addr = evaluate(cmd[0])
        if not header_addr:
            print("[-] error: invalid header address value.")
            print("")
            print(help)
            return        
    else:
        print("[-] error: please insert a valid Mach-O header address.")
        print("")
        print(help)
        return

    if os.path.isfile("/usr/bin/otool") == False:
            print("/usr/bin/otool not found. Please install Xcode or Xcode command line tools.")
            return
    
    # recent otool versions will fail so we need to read a reasonable amount of memory
    # even just for the mach-o header
    bytes_string = get_process().ReadMemory(header_addr, 4096*10, error)
    if error.Success() == False:
        print("[-] error: Failed to read memory at 0x{:x}.".format(header_addr))
        return

    # open a temporary filename and set it to delete on close
    f = tempfile.NamedTemporaryFile(delete=True)
    f.write(bytes_string)
    # pass output to otool
    output_data = subprocess.check_output(["/usr/bin/otool", "-hv", f.name])
    # show the data
    print(output_data)
    # close file - it will be automatically deleted
    f.close()

    return

# use keystone-engine.org to assemble
def assemble_keystone(arch, mode, code, syntax=0):
    ks = Ks(arch, mode)
    if syntax != 0:
        ks.syntax = syntax

    print("\nKeystone output:\n----------")
    for inst in code:
        try:
            encoding, count = ks.asm(inst)
        except KsError as e:
            print("[-] error: keystone failed to assemble: {:s}".format(e))
            return
        output = []
        output.append(inst)
        output.append('->')
        for i in encoding:
            output.append("{:02x}".format(i))
        print(" ".join(output))

def cmd_asm32(debugger, command, result, dict):
    '''32 bit x86 interactive Keystone based assembler. Use \'asm32 help\' for more information.'''
    help = """
32 bit x86 interactive Keystone based assembler.

Syntax: asm32

Type one instruction per line. Finish with \'end\' or \'stop\'.
Keystone set to KS_ARCH_X86 and KS_MODE_32.

Requires Keystone and Python bindings from www.keystone-engine.org.
"""
    cmd = command.split()
    if len(cmd) != 0 and cmd[0] == "help":
        print(help)
        return

    if CONFIG_KEYSTONE_AVAILABLE == 0:
        print("[-] error: keystone python bindings not available. please install from www.keystone-engine.org.")
        return
    
    inst_list = []
    while True:
        line = input('Assemble ("stop" or "end" to finish): ')
        if line == 'stop' or line == 'end':
            break
        inst_list.append(line)
    
    assemble_keystone(KS_ARCH_X86, KS_MODE_32, inst_list)

def cmd_asm64(debugger, command, result, dict):
    '''64 bit x86 interactive Keystone based assembler. Use \'asm64 help\' for more information.'''
    help = """
64 bit x86 interactive Keystone based assembler

Syntax: asm64

Type one instruction per line. Finish with \'end\' or \'stop\'.
Keystone set to KS_ARCH_X86 and KS_MODE_64.

Requires Keystone and Python bindings from www.keystone-engine.org.
"""
    cmd = command.split()
    if len(cmd) != 0 and cmd[0] == "help":
        print(help)
        return

    if CONFIG_KEYSTONE_AVAILABLE == 0:
        print("[-] error: keystone python bindings not available. please install from www.keystone-engine.org.")
        return
    
    inst_list = []
    while True:
        line = input('Assemble ("stop" or "end" to finish): ')
        if line == 'stop' or line == 'end':
            break
        inst_list.append(line)
    
    assemble_keystone(KS_ARCH_X86, KS_MODE_64, inst_list)

def cmd_arm32(debugger, command, result, dict):
    '''32 bit ARM interactive Keystone based assembler. Use \'arm32 help\' for more information.'''
    help = """
32 bit ARM interactive Keystone based assembler

Syntax: arm32

Type one instruction per line. Finish with \'end\' or \'stop\'.
Keystone set to KS_ARCH_ARM and KS_MODE_ARM.
    
Requires Keystone and Python bindings from www.keystone-engine.org.
"""
    cmd = command.split()
    if len(cmd) != 0 and cmd[0] == "help":
        print(help)
        return

    if CONFIG_KEYSTONE_AVAILABLE == 0:
        print("[-] error: keystone python bindings not available. please install from www.keystone-engine.org.")
        return
    
    inst_list = []
    while True:
        line = input('Assemble ("stop" or "end" to finish): ')
        if line == 'stop' or line == 'end':
            break
        inst_list.append(line)
    
    assemble_keystone(KS_ARCH_ARM, KS_MODE_ARM, inst_list)

def cmd_armthumb(debugger, command, result, dict):
    '''32 bit ARM Thumb interactive Keystone based assembler. Use \'armthumb help\' for more information.'''
    help = """
32 bit ARM Thumb interactive Keystone based assembler

Syntax: armthumb

Type one instruction per line. Finish with \'end\' or \'stop\'.
Keystone set to KS_ARCH_ARM and KS_MODE_THUMB.

Requires Keystone and Python bindings from www.keystone-engine.org.
"""
    cmd = command.split()
    if len(cmd) != 0 and cmd[0] == "help":
        print(help)
        return

    if CONFIG_KEYSTONE_AVAILABLE == 0:
        print("[-] error: keystone python bindings not available. please install from www.keystone-engine.org.")
        return
    
    inst_list = []
    while True:
        line = input('Assemble ("stop" or "end" to finish): ')
        if line == 'stop' or line == 'end':
            break
        inst_list.append(line)
    
    assemble_keystone(KS_ARCH_ARM, KS_MODE_THUMB, inst_list)

def cmd_arm64(debugger, command, result, dict):
    '''64 bit ARM interactive Keystone based assembler. Use \'arm64 help\' for more information.'''
    help = """
64 bit ARM interactive Keystone based assembler

Syntax: arm64

Type one instruction per line. Finish with \'end\' or \'stop\'.
Keystone set to KS_ARCH_ARM64 and KS_MODE_ARM.

Requires Keystone and Python bindings from www.keystone-engine.org.
"""
    cmd = command.split()
    if len(cmd) != 0 and cmd[0] == "help":
        print(help)
        return

    if CONFIG_KEYSTONE_AVAILABLE == 0:
        print("[-] error: keystone python bindings not available. please install from www.keystone-engine.org.")
        return
    
    inst_list = []
    while True:
        line = input('Assemble ("stop" or "end" to finish): ')
        if line == 'stop' or line == 'end':
            break
        inst_list.append(line)
    
    assemble_keystone(KS_ARCH_ARM64, KS_MODE_ARM, inst_list)

# XXX: help
def cmd_IphoneConnect(debugger, command, result, dict): 
    '''Connect to debugserver running on iPhone'''
    global GlobalListOutput
    GlobalListOutput = []
        
    if len(command) == 0 or ":" not in command:
        output("Connect to remote iPhone debug server")
        output("\n")
        output("iphone <ipaddress:port>")
        output("\n")
        output("iphone 192.168.0.2:5555")
        result.PutCString("".join(GlobalListOutput))
        result.SetStatus(lldb.eReturnStatusSuccessFinishResult)
        return

    res = lldb.SBCommandReturnObject()
    lldb.debugger.GetCommandInterpreter().HandleCommand("platform select remote-ios", res)
    if res.Succeeded():
        output(res.GetOutput())
    else:
        output("[-] Error running platform select remote-ios")
        result.PutCString("".join(GlobalListOutput))
        result.SetStatus(lldb.eReturnStatusSuccessFinishResult)
        return
    lldb.debugger.GetCommandInterpreter().HandleCommand("process connect connect://" + command, res)
    if res.Succeeded():
        output("[+] Connected to iphone at : " + command)
    else:
        output(res.GetOutput())
    result.PutCString("".join(GlobalListOutput))
    result.SetStatus(lldb.eReturnStatusSuccessFinishResult)

# xnu kernel debug support command
def cmd_xnu_kdp_reboot(debugger, command, result, dict):
    '''
        Restart debuggee vm
    '''
    if GetConnectionProtocol() != 'kdp':
        print('Target is not connect over kdp')
        return False
    
    print('[+] Reboot the remote machine')
    lldb.debugger.HandleCommand('process plugin packet send --command 0x13')
    lldb.debugger.HandleCommand('detach')
    return True

def cmd_xnu_show_bootargs(debugger, command, result, dict):
    boot_args = xnu_showbootargs(debugger.GetSelectedTarget())
    if not boot_args:
        print('Please use kernel.development to boot macOS')
        return False
    
    print('[+] macOS boot-args:', repr(boot_args))
    return True

def cmd_xnu_panic_log(debugger, command, result, dict):

    args = command.split(' ')
    if len(args) > 1:
        print('panic_log <save path | empty>')
        return False
    
    panic_log = xnu_panic_log(debugger.GetSelectedTarget())
    
    if len(args) == 1 and args[0]:
        log_file = args[0]
        print(f'[+] Saving panic_log to {log_file}')
        f = open(log_file, 'wb')
        f.write(panic_log)
        f.close()
    else:
        print('---- Panic Log ----')
        print(panic_log.decode('utf-8'))
    return True

# xnu zones command

def cmd_xnu_list_zone(debugger, command, result, dict):
    global XNU_ZONES
    if not XNU_ZONES:
        XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())
    
    print('[+] Zones:')
    pad_size = len(str(len(XNU_ZONES)))
    for i in range(len(XNU_ZONES)):
        zone_name = XNU_ZONES.getZoneName(XNU_ZONES[i])
        print(f'- {i:{pad_size}} | {zone_name}')
    
def cmd_xnu_find_zones_by_name(debugger, command, result, dict):
    global XNU_ZONES
    if not XNU_ZONES:
        XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())

    args = command.split(' ')
    if len(args) < 1:
        print('zone_find_zones_index <zone name>')
        return False

    zones = XNU_ZONES.findzone_by_names(args[0])

    print('[+] Zones:')
    pad_size = len(str(len(zones)))
    for i, zone in zones:
        print(f'- {i:{pad_size}} | {XNU_ZONES.getZoneName(zone)}')

def cmd_xnu_zshow_logged_zone(debugger, command, result, dict):
    global XNU_ZONES
    if not XNU_ZONES:
        XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())
    
    XNU_ZONES.show_zone_being_logged()

def cmd_xnu_zone_triage(debugger, command, result, _dict):
    global XNU_ZONES
    if not XNU_ZONES:
        XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())

    args = command.split(' ')
    if len(args) < 2:
        print('zone_triage: <zone_name> <element_ptr>')
        return False
    
    zone_name = args[0]
    elem_ptr = evaluate(args[1])
    zone_idx = XNU_ZONES.getLoggedZoneIdxByName(zone_name)
    if zone_idx < 0:
        print(f'[!] Invalid zone name : "{zone_name}"')
        return False
    
    if not XNU_ZONES.is_zonelogging(zone_idx):
        print(f'[!] Zone name "{zone_name}" is not logging')
        return False
    
    if not elem_ptr:
        print('[!] Invalid elem_ptr')
        return False
    
    XNU_ZONES.zone_find_stack_elem(zone_idx, elem_ptr)

    return True

def cmd_xnu_inspect_zone(debugger, command, result, _dict):
    global XNU_ZONES
    if not XNU_ZONES:
        XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())
    
    if not len(command):
        print('zone_inspect: <zone_name>')
        return False
    
    zone_name = command
    zone_idx = XNU_ZONES.getZoneIdxbyName(zone_name)
    if zone_idx < 0:
        print(f'[!] Invalid zone name : "{zone_name}"')
        return False

    XNU_ZONES.InspectZone(zone_idx)
    return True

def cmd_xnu_show_chunk_at(debugger, command, result, _dict):
    global XNU_ZONES
    if not XNU_ZONES:
        XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())
    
    args = command.split(' ')
    if len(args) < 2:
        print('zone_show_chunk_at: <zone_name> <chunk_addr>')
        return False
    
    zone_name = args[0]
    chunk_addr = evaluate(args[1])

    zone_idx = XNU_ZONES.getZoneIdxbyName(zone_name)
    status = XNU_ZONES.GetChunkInfoAtZone(zone_idx, chunk_addr)
    if status != 'None':
        color = COLORS["GREEN"]
        if status == 'Freed':
            color = COLORS["RED"]

        print(f'[+] zone_array[{zone_idx}]({zone_name}) - {COLORS["BOLD"]}0x{chunk_addr:X}{COLORS["RESET"]}{color} ({status})')
        print(COLORS["RESET"], end='')
    else:
        print(f'[+] Your chunk address is not found in zone {zone_name}.')
    
    return True

def cmd_xnu_show_chunk_with_regex(debugger, command, result, _dict):
    global XNU_ZONES
    if not XNU_ZONES:
        XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())
    
    args = command.split(' ')
    if len(args) < 2:
        print('zone_show_chunk_with_regex: <zone_name_regex> <chunk_addr>')
        return False
    
    zone_name_regex = args[0]
    chunk_addr = evaluate(args[1])

    if zone_name_regex == 'kalloc':
        zone_name_regex = '.*kalloc.*' # quickway to find kalloc zone

    zone_idxs = XNU_ZONES.getZonebyRegex(zone_name_regex)
    if not zone_idxs:
        print('[+] Your chunk address is not found in any zones.')
        return True
    
    for zone_idx in zone_idxs:
        zone_name = XNU_ZONES.getZoneName(XNU_ZONES[zone_idx])
        print(f'[+] Searching on zone: {zone_name}')

        status = XNU_ZONES.GetChunkInfoAtZone(zone_idx, chunk_addr)
        if status != 'None':
            color = COLORS["GREEN"]
            if status == 'Freed':
                color = COLORS["RED"]

            print(f'[+] zone_array[{zone_idx}]({zone_name}) - {COLORS["BOLD"]}0x{chunk_addr:X}{COLORS["RESET"]}{color}({status})')
            print(COLORS["RESET"], end='')
            break
    
    return True

def cmd_xnu_zone_backtrace_at(debugger, command, result, _dict):
    global XNU_ZONES
    if not XNU_ZONES:
        XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())
    
    args = command.split(' ')
    action = 1 # get backtrace history of free chunk pointer
    if len(args) < 2:
        print('Usage: zone_backtrace_at <zone_name> <chunk_ptr> <action>')
        print('action: 1 for kfree backtrace only ')
        print('        2 for kmalloc backtrace only ')
        return False

    try:
        zone_name = args[0]
        chunk_ptr = evaluate(args[1])
        action = int(args[2])
    except IndexError:
        pass
    
    zone_idx = XNU_ZONES.getLoggedZoneIdxByName(zone_name)
    if zone_idx < 0:
        print(f'[!] Invalid zone name : "{zone_name}"')
        return False
    
    if not XNU_ZONES.is_zonelogging(zone_idx):
        print(f'[!] Zone name "{zone_name}" is not logging')
        return False
    
    if not chunk_ptr:
        print('[!] Invalid chunk ptr')
        return False
    
    XNU_ZONES.zone_find_stack_elem(zone_idx, chunk_ptr, action)
    return True
    
def cmd_xnu_find_chunk(debugger, command, result, _dict):
    global XNU_ZONES
    if not XNU_ZONES:
        XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())
    
    if not len(command):
        print('zone_find_chunk: <chunk_addr>')
        return False
    
    chunk_addr = evaluate(command)
    
    info = XNU_ZONES.FindChunkInfo(chunk_addr)
    if info != None:
        zone_name = info['zone_name']
        zone_idx = info['zone_idx']
        status = info['status']
        print(f'[+] zone_array[{zone_idx}] ({zone_name}) - 0x{chunk_addr:X}({status})')
    else:
        print('[+] Your chunk address is not found in any zones.')
    
    return True

def cmd_xnu_zone_reload(debugger, command, result, _dict):
    global XNU_ZONES
    print('[+] Reload XNU_ZONES')
    XNU_ZONES = XNUZones(lldb.debugger.GetSelectedTarget())

def cmd_xnu_showallkexts(debugger, command, result, dict):
    kexts = xnu_get_all_kexts()

    longest_kext_name = len(max(kexts, key=lambda x: len(x[0]))[0])
    
    print('-- Loaded kexts:')
    for kext_name, kext_uuid, kext_address, kext_size in kexts:
        print(f'+ {kext_name:{longest_kext_name}}\t{kext_uuid}\t\t0x{kext_address:X}\t{kext_size}')

def cmd_xnu_breakpoint(debugger, command, result, dict):
    args = command.split(' ')
    if len(args) < 2:
        print('kbp <kext_name> <offset>')
        return

    kext_name = args[0]
    offset = int(args[1], 16)

    base_address = xnu_get_kext_base_address(kext_name)
    if base_address == 0:
        print(f'[!] Couldn\'t found base address of kext {kext_name}')
        return

    target_address = offset + base_address

    target = debugger.GetSelectedTarget()
    target.BreakpointCreateByAddress(target_address)
    print('Done')

def cmd_xnu_to_offset(debugger, command, result, dict):
    args = command.split(' ')
    if len(args) < 2:
        print('showallproc <kext_name> <address>')
        return

    kext_name = args[0]
    address = evaluate(args[1])

    base_address = xnu_get_kext_base_address(kext_name)
    if base_address == 0:
        print(f'[!] Couldn\'t found base address of kext {kext_name}')
        return

    offset = address - base_address
    print(f'Offsset from Kext {kext_name} base address : 0x{offset:X}')

def cmd_xnu_list_all_process(debugger, command, result, dict):
    xnu_list_all_process()

def cmd_xnu_search_process_by_name(debugger, command, result, dict):
    args = command.split(' ')
    if len(args) < 1:
        print('showproc <process name>')
        return

    proc_name = args[0]
    xnu_proc = xnu_search_process_by_name(proc_name)
    if xnu_proc == None:
        print(f'[!] Couldn\'t found your process {proc_name}')
        return

    proc_name = xnu_proc.p_name.GetStrValue()
    p_pid = xnu_proc.p_pid.GetValue()

    print(f'+ {p_pid} - {proc_name} - {xnu_proc.GetValue()}')

def cmd_xnu_read_usr_addr(debugger, command, result, dict):
    args = command.split(' ')
    if len(args) < 3:
        print('readusraddr <proc_name> <user space address> <size>')
        return

    process_name = args[0]
    proc = xnu_search_process_by_name(process_name)
    if proc == None:
        print('[!] Process does not found.')
        return
    
    user_space_addr = evaluate(args[1])
    try:
        size = int(args[2])
    except (TypeError, ValueError):
        size = 0x20

    raw_data = xnu_read_user_address(debugger.GetSelectedTarget(), proc.task, user_space_addr, size)
    print(hexdump(user_space_addr, raw_data, " ", 16))

def cmd_xnu_set_kdp_pmap(debugger, command, result, dict):
    if GetConnectionProtocol() != 'kdp':
        print('[!] cmd_xnu_set_kdp_pmap() only works on kdp-remote')
        return

    args = command.split(' ')
    if len(args) < 1:
        print('setkdp <process name>')
        return
    
    target_proc = xnu_search_process_by_name(args[0])
    if target_proc == None:
        print(f'[!] Process {args[0]} does not found')
        return
    
    if xnu_write_task_kdp_pmap(debugger.GetSelectedTarget(), target_proc.task):
        print('[+] Set kdp_pmap ok.')
    else:
        print('[!] Set kdp_pmap failed.')

def cmd_xnu_reset_kdp_pmap(debugger, command, result, dict):
    if GetConnectionProtocol() != 'kdp':
        print('[!] cmd_xnu_set_kdp_pmap() only works on kdp-remote')
        return

    if not xnu_reset_kdp_pmap(debugger.GetSelectedTarget()):
        print(f'[!] Reset kdp_pmap failed.')
        return

    print('[+] Reset kdp_pmap ok.')

## VMware / VirtualBox commands

def cmd_vm_show_vm(debugger, command, result, dict):
    if not vmfusion_check():
        print('[!] This feature only support Vmware Fusion')
        return

    running_vms = get_all_running_vm()

    if not running_vms:
        print('[!] No virtual machine is running.')
        return

    for vm_name in running_vms:
        print(f'- {vm_name} : {running_vms[vm_name]}')

def cmd_vm_select_vm(debugger, command, result, dict):
    if not vmfusion_check():
        print('[!] This feature only support Vmware Fusion')
        return

    global SelectedVM

    args = command.split('\n')
    if len(args) < 1:
        print('vmselect <vm_name>')
        return

    vm_name = args[0]
    running_vms = get_all_running_vm()
    if vm_name not in running_vms:
        print(f'[!] Couldn found your vm name {vm_name}')
        return

    SelectedVM = running_vms[vm_name]

def cmd_vm_take_snapshot(debugger, command, result, dict):
    if not vmfusion_check():
        print('[!] This feature only support Vmware Fusion')
        return

    global SelectedVM

    args = command.split('\n')
    if len(args) < 1:
        print('vmsnapshot <snapshot name>')
        return

    if not SelectedVM:
        print('[!] Please run `vmselect` to select your vm')
        return

    take_vm_snapshot(SelectedVM, args[0])
    print('Done.')

def cmd_vm_reverse_snapshot(debugger, command, result, dict):
    if not vmfusion_check():
        print('[!] This feature only support Vmware Fusion')
        return

    global SelectedVM

    args = command.split('\n')
    if len(args) < 1:
        print('vmreverse <snapshot name>')
        return

    if not SelectedVM:
        print('[!] Please run `vmselect` to select your vm')
        return

    revert_vm_snapshot(SelectedVM, args[0])
    print('Done.')

def cmd_vm_delete_snapshot(debugger, command, result, dict):
    if not vmfusion_check():
        print('[!] This feature only support Vmware Fusion')
        return

    global SelectedVM

    args = command.split('\n')
    if len(args) < 1:
        print('vmreverse <snapshot name>')
        return

    if not SelectedVM:
        print('[!] Please run `vmselect` to select your vm')
        return

    delete_vm_snapshot(SelectedVM, args[0])
    print('Done.')

def cmd_vm_list_snapshot(debugger, command, result, dict):
    if not vmfusion_check():
        print('[!] This feature only support Vmware Fusion')
        return

    global SelectedVM
    
    if not SelectedVM:
        print('[!] Please run `vmselect` to select your vm')
        return

    snapshots = list_vm_snapshot(SelectedVM)

    print('Current snapshot:')
    for snapshot in snapshots:
        print('-', snapshot)

# ------------------------------------------------------------------------------------------- #

def display_stack():
    '''Hex dump current stack pointer'''
    stack_addr = get_current_sp()
    if stack_addr == 0:
        return
    err = lldb.SBError()
    membuff = get_process().ReadMemory(stack_addr, 0x100, err)
    if err.Success() == False:
        print("[-] error: Failed to read memory at 0x{:x}.".format(stack_addr))
        return
    if len(membuff) == 0:
        print("[-] error: not enough bytes read.")
        return

    output(hexdump(stack_addr, membuff, " ", 16, 4))

def display_data():
    '''Hex dump current data window pointer'''
    data_addr = DATA_WINDOW_ADDRESS
    if data_addr == 0:
        return
    err = lldb.SBError()
    membuff = get_process().ReadMemory(data_addr, 0x100, err)
    if err.Success() == False:
        print("[-] error: Failed to read memory at 0x{:x}.".format(data_addr))
        return
    if len(membuff) == 0:
        print("[-] error: not enough bytes read.")
        return

    output(hexdump(data_addr, membuff, " ", 16, 4))

# workaround for lldb bug regarding RIP addressing outside main executable
def get_rip_relative_addr(source_address):
    err = lldb.SBError()
    inst_size = get_inst_size(source_address)
    if inst_size <= 1:
        print("[-] error: instruction size too small.")
        return 0
    # XXX: problem because it's not just 2 and 5 bytes
    # 0x7fff53fa2180 (0x1180): 0f 85 84 01 00 00     jne    0x7fff53fa230a ; stack_not_16_byte_aligned_error

    offset_bytes = get_process().ReadMemory(source_address+1, inst_size-1, err)
    if err.Success() == False:
        print("[-] error: Failed to read memory at 0x{:x}.".format(source_address))
        return 0
    if inst_size == 2:
        data = struct.unpack("b", offset_bytes)
    elif inst_size == 5:
        data = struct.unpack("i", offset_bytes)
    rip_call_addr = source_address + inst_size + data[0]
    #output("source {:x} rip call offset {:x} {:x}\n".format(source_address, data[0], rip_call_addr))
    return rip_call_addr

# XXX: instead of reading memory we can dereference right away in the evaluation
def get_indirect_flow_target(source_address):
    err = lldb.SBError()
    operand = get_operands(source_address)
    operand = operand.lower()
    #output("Operand: {}\n".format(operand))
    # calls into a deferenced memory address
    if "qword" in operand:
        #output("dereferenced call\n")
        deref_addr = 0
        # first we need to find the address to dereference
        if '+' in operand:
            x = re.search('\[([a-z0-9]{2,3} \+ 0x[0-9a-z]+)\]', operand)
            if x == None:
                return 0
            value = get_frame().EvaluateExpression("$" + x.group(1))
            if value.IsValid() == False:
                return 0
            deref_addr = int(value.GetValue(), 10)
            if "rip" in operand:
                deref_addr = deref_addr + get_inst_size(source_address)
        else:
            x = re.search('\[([a-z0-9]{2,3})\]', operand)
            if x == None:
                return 0
            value = get_frame().EvaluateExpression("$" + x.group(1))
            if value.IsValid() == False:
                return 0
            deref_addr = int(value.GetValue(), 10)
        # now we can dereference and find the call target
        if get_pointer_size() == 4:
            call_target_addr = get_process().ReadUnsignedFromMemory(deref_addr, 4, err)
            return call_target_addr
        elif get_pointer_size() == 8:
            call_target_addr = get_process().ReadUnsignedFromMemory(deref_addr, 8, err)
            return call_target_addr
        if err.Success() == False:
            return 0
        return 0        
    # calls into a register included x86_64 and aarch64
    elif operand.startswith('r') or operand.startswith('e') or operand.startswith('x') or \
            operand in ('lr', 'sp', 'fp'):
        #output("register call\n")
        # x = re.search('([a-z0-9]{2,3})', operand)
        # if x == None:
        #   return 0

        #output("Result {}\n".format(x.group(1)))
        value = get_frame().EvaluateExpression("$" + operand)
        if value.IsValid() == False:
            return 0
        return int(value.GetValue(), 10)
    # RIP relative calls
    elif operand.startswith('0x'):
        #output("direct call\n")
        # the disassembler already did the dirty work for us
        # so we just extract the address
        x = re.search('(0x[0-9a-z]+)', operand)
        if x != None:
            #output("Result {}\n".format(x.group(0)))
            return int(x.group(1), 16)
    return 0

def get_ret_address():
    err = lldb.SBError()
    stack_addr = get_current_sp()
    if stack_addr == 0:
        return -1
    ret_addr = get_process().ReadPointerFromMemory(stack_addr, err)
    if err.Success() == False:
        print("[-] error: Failed to read memory at 0x{:x}.".format(stack_addr))
        return -1
    return ret_addr

def is_sending_objc_msg():
    err = lldb.SBError()

    call_addr = get_indirect_flow_target(get_current_pc())
    symbol_name = resolve_symbol_name(call_addr)
    return symbol_name == "objc_msgSend"

# XXX: x64 only
def display_objc():
    err = lldb.SBError()

    options = lldb.SBExpressionOptions()
    options.SetLanguage(lldb.eLanguageTypeObjC)
    options.SetTrapExceptions(False)

#    command = '(void*)object_getClass({})'.format(get_instance_object())
#    value = get_frame().EvaluateExpression(command, options).GetObjectDescription()
    className = objc_get_classname(get_instance_object())
    if not className:
        return
    
    if is_x64():
        selector_addr = get_gp_register("rsi")
    elif is_aarch64():
        selector_addr = get_gp_register("x1")
    else:
        return

    membuff = get_process().ReadMemory(selector_addr, 0x100, err)
    selector = membuff.split(b'\x00')
    if len(selector) != 0:
        color("RED")
        output('Class: ')
        color("RESET")
        output(className)
        color("RED")
        output(' Selector: ')
        color("RESET")
        output(selector[0].decode('utf-8'))

def display_indirect_flow():
    pc_addr = get_current_pc()
    mnemonic = get_mnemonic(pc_addr)

    if ("ret" in mnemonic):
        indirect_addr = get_ret_address()
        output("0x%x -> %s" % (indirect_addr, resolve_symbol_name(indirect_addr)))
        output("\n")
        return
    
    if ("call" == mnemonic) or "callq" == mnemonic or ("jmp" in mnemonic):
        # we need to identify the indirect target address
        indirect_addr = get_indirect_flow_target(pc_addr)
        output("0x%x -> %s" % (indirect_addr, resolve_symbol_name(indirect_addr)))

        if is_sending_objc_msg():
            output("\n")
            display_objc()
        output("\n")
    
    if ("br" == mnemonic) or ("bl" == mnemonic) or ("b" == mnemonic):
        indirect_addr = get_indirect_flow_target(pc_addr)
        output("0x%x -> %s" % (indirect_addr, resolve_symbol_name(indirect_addr)))

        if is_sending_objc_msg():
            output("\n")
            display_objc()
        output("\n")

    return

# find out the target address of ret, and indirect call and jmp
def get_indirect_flow_address(src_addr):
    target = get_target()
    instruction_list = target.ReadInstructions(lldb.SBAddress(src_addr, target), 1, 'intel')
    if instruction_list.GetSize() == 0:
        print("[-] error: not enough instructions disassembled.")
        return -1

    cur_instruction = instruction_list.GetInstructionAtIndex(0)
    if not cur_instruction.DoesBranch():
        return -1

    mnemonic = cur_instruction.GetMnemonic(target)
    # if "ret" in cur_instruction.mnemonic:
    if 'ret' in mnemonic:
        if is_aarch64():
            ret_addr = get_gp_register('x30')
            if ret_addr == 0:
                ret_addr = get_gp_register('lr')
            
            return ret_addr

        ret_addr = get_ret_address()
        return ret_addr
    # if ("call" in cur_instruction.mnemonic) or ("jmp" in cur_instruction.mnemonic):
    # trace both x86_64 and arm64
    if mnemonic in ('call', 'jmp') or mnemonic in ('bl', 'br', 'b', 'blr'):
        # don't care about RIP relative jumps
        # if cur_instruction.operands.startswith('0x'):
        if cur_instruction.GetOperands(target).startswith('0x'):
            return -1
        indirect_addr = get_indirect_flow_target(src_addr)
        return indirect_addr

    # all other branches just return -1
    return -1

# retrieve the module full path name an address belongs to
def get_module_name(src_addr):
    target = get_target()
    src_module = lldb.SBAddress(src_addr, target).module
    module_name = src_module.file.fullpath
    if module_name == None:
        return ""
    else:
        return module_name

def get_objectivec_selector_at(call_addr):
    symbol_name = resolve_symbol_name(call_addr)
    if not symbol_name:
        return ''

    # XXX: add others?
    if (not symbol_name.startswith("objc_msgSend")) and \
            (symbol_name not in ('objc_alloc', 'objc_opt_class')):
        return ""
    
    options = lldb.SBExpressionOptions()
    options.SetLanguage(lldb.eLanguageTypeObjC)
    options.SetTrapExceptions(False)

    classname_command = '(const char *)object_getClassName((id){})'.format(get_instance_object())
    classname_value = get_frame().EvaluateExpression(classname_command)
    if classname_value.IsValid() == False:
        return ""
        
    className_summary = classname_value.GetSummary()
    if className_summary:
        className = className_summary.strip('"')

        if symbol_name.startswith("objc_msgSend"):
            if is_x64():
                selector_addr = get_gp_register("rsi")
            else:
                selector_addr = get_gp_register("x1")
            
            err = lldb.SBError()
            membuf = get_process().ReadMemory(selector_addr, 0x100, err)
            selector = membuf.split(b'\00')
            if len(selector) != 0:
                return "[" + className + " " + selector[0].decode('utf-8') + "]"
            else:
                return "[" + className + "]"
        else:
            return "{0}({1})".format(symbol_name, className)
    
    return ""

def get_objectivec_selector(src_addr):

    if not is_x64() and not is_aarch64():
        return ''

    call_addr = get_indirect_flow_target(src_addr)
    if call_addr == 0:
        return ""
        
    return get_objectivec_selector_at(call_addr)

# ------------------------------------------------------------
# The heart of lldbinit - when lldb stop this is where we land 
# ------------------------------------------------------------

def print_cpu_registers(register_names):
    registers = get_gp_registers()
    break_flag = False
    reg_flag_val = -1

    for i, register_name in enumerate(register_names):
        try:
            reg_val = registers[register_name]
        except KeyError:
            if is_aarch64():
                if register_name == 'x29':
                    register_name = 'fp'
                    reg_val = registers['fp']
                elif register_name == 'x30':
                    register_name == 'lr'
                    reg_val = registers['lr']

        if register_name in flag_regs:
            output("  ")
            color("BOLD")
            color("UNDERLINE")
            color(COLOR_CPUFLAGS)
            if is_arm() or is_aarch64():
                dump_cpsr(reg_val)
            elif is_i386() or is_x64():
                dump_eflags(reg_val)
            color("RESET")
            reg_flag_val = reg_val
        
        else:

            if (not break_flag) and (register_name in segment_regs):
                output('\n')
                break_flag = True


            color(COLOR_REGNAME)
            output("  {0:<3}: ".format(register_name.upper().ljust(3, ' ')))

            try:

                if register_name in ('rsp', 'esp', 'sp'):
                    color("BLUE")

                elif register_name in ('rip', 'eip', 'pc'):
                    color("RED")

                else:
                    if reg_val == old_register[register_name]:
                        color(get_color_status(reg_val))
                    else:
                        color(COLOR_REGVAL_MODIFIED)
            except KeyError:
                color(get_color_status(reg_val))

            if register_name in segment_regs:
                output("%.04X" % (reg_val))
            else:
                if is_x64() or is_aarch64():
                    output("0x%.016lX" % (reg_val))
                else:
                    output("0x%.08X" % (reg_val))

            old_register[register_name] = reg_val

        if (not break_flag) and (i % 4 == 0) and i != 0:
            output('\n')

    if is_x64() or is_i386():
        dump_jumpx86(reg_flag_val)
    elif is_aarch64():
        dump_jump_arm64(reg_flag_val)
    
    output("\n")
        
def dump_eflags(eflags):
    # the registers are printed by inverse order of bit field
    # no idea where this comes from :-]
    # masks = { "CF":0, "PF":2, "AF":4, "ZF":6, "SF":7, "TF":8, "IF":9, "DF":10, "OF":11 }
    # printTuples = sorted(masks.items() , reverse=True, key=lambda x: x[1])
    eflagsTuples = [('OF', 11), ('DF', 10), ('IF', 9), ('TF', 8), ('SF', 7), ('ZF', 6), ('AF', 4), ('PF', 2), ('CF', 0)]
    # use the first character of each register key to output, lowercase if bit not set
    for flag, bitfield in eflagsTuples :
        if bool(eflags & (1 << bitfield)) == True:
            output(flag[0] + " ")
        else:
            output(flag[0].lower() + " ")

# function to dump the conditional jumps results
def dump_jumpx86(eflags):
    # masks and flags from https://github.com/ant4g0nist/lisa.py
    masks = { "CF":0, "PF":2, "AF":4, "ZF":6, "SF":7, "TF":8, "IF":9, "DF":10, "OF":11 }
    flags = { key: bool(eflags & (1 << value)) for key, value in masks.items() }

    if is_i386():
        pc_addr = get_gp_register("eip")
    elif is_x64():
        pc_addr = get_gp_register("rip")
    else:
        print("[-] dump_jumpx86() error: wrong architecture.")
        return

    mnemonic = get_mnemonic(pc_addr)
    color("RED")
    output_string=""
    ## opcode 0x77: JA, JNBE (jump if CF=0 and ZF=0)
    ## opcode 0x0F87: JNBE, JA
    if "ja" == mnemonic or "jnbe" == mnemonic:
        if flags["CF"] == False and flags["ZF"] == False:
            output_string="Jump is taken (c = 0 and z = 0)"
        else:
            output_string="Jump is NOT taken (c = 0 and z = 0)"
    ## opcode 0x73: JAE, JNB, JNC (jump if CF=0)
    ## opcode 0x0F83: JNC, JNB, JAE (jump if CF=0)
    elif "jae" == mnemonic or "jnb" == mnemonic or "jnc" == mnemonic:
        if flags["CF"] == False:
            output_string="Jump is taken (c = 0)"
        else:
            output_string="Jump is NOT taken (c != 0)"
    ## opcode 0x72: JB, JC, JNAE (jump if CF=1)
    ## opcode 0x0F82: JNAE, JB, JC
    elif "jb" == mnemonic or "jc" == mnemonic or "jnae" == mnemonic:
        if flags["CF"] == True:
            output_string="Jump is taken (c = 1)"
        else:
            output_string="Jump is NOT taken (c != 1)"
    ## opcode 0x76: JBE, JNA (jump if CF=1 or ZF=1)
    ## opcode 0x0F86: JBE, JNA
    elif "jbe" == mnemonic or "jna" == mnemonic:
        if flags["CF"] == True or flags["ZF"] == 1:
            output_string="Jump is taken (c = 1 or z = 1)"
        else:
            output_string="Jump is NOT taken (c != 1 or z != 1)"
    ## opcode 0xE3: JCXZ, JECXZ, JRCXZ (jump if CX=0 or ECX=0 or RCX=0)
    # XXX: we just need cx output...
    elif "jcxz" == mnemonic or "jecxz" == mnemonic or "jrcxz" == mnemonic:
        rcx = get_gp_register("rcx")
        ecx = get_gp_register("ecx")
        cx = get_gp_register("cx")
        if ecx == 0 or cx == 0 or rcx == 0:
            output_string="Jump is taken (cx = 0 or ecx = 0 or rcx = 0)"
        else:
            output_string="Jump is NOT taken (cx != 0 or ecx != 0 or rcx != 0)"
    ## opcode 0x74: JE, JZ (jump if ZF=1)
    ## opcode 0x0F84: JZ, JE, JZ (jump if ZF=1)
    elif "je" == mnemonic or "jz" == mnemonic:
        if flags["ZF"] == 1:
            output_string="Jump is taken (z = 1)"
        else:
            output_string="Jump is NOT taken (z != 1)"
    ## opcode 0x7F: JG, JNLE (jump if ZF=0 and SF=OF)
    ## opcode 0x0F8F: JNLE, JG (jump if ZF=0 and SF=OF)
    elif "jg" == mnemonic or "jnle" == mnemonic:
        if flags["ZF"] == 0 and flags["SF"] == flags["OF"]:
            output_string="Jump is taken (z = 0 and s = o)"
        else:
            output_string="Jump is NOT taken (z != 0 or s != o)"
    ## opcode 0x7D: JGE, JNL (jump if SF=OF)
    ## opcode 0x0F8D: JNL, JGE (jump if SF=OF)
    elif "jge" == mnemonic or "jnl" == mnemonic:
        if flags["SF"] == flags["OF"]:
            output_string="Jump is taken (s = o)"
        else:
            output_string="Jump is NOT taken (s != o)"
    ## opcode: 0x7C: JL, JNGE (jump if SF != OF)
    ## opcode: 0x0F8C: JNGE, JL (jump if SF != OF)
    elif "jl" == mnemonic or "jnge" == mnemonic:
        if flags["SF"] != flags["OF"]:
            output_string="Jump is taken (s != o)"
        else:
            output_string="Jump is NOT taken (s = o)"
    ## opcode 0x7E: JLE, JNG (jump if ZF = 1 or SF != OF)
    ## opcode 0x0F8E: JNG, JLE (jump if ZF = 1 or SF != OF)
    elif "jle" == mnemonic or "jng" == mnemonic:
        if flags["ZF"] == 1 or flags["SF"] != flags["OF"]:
            output_string="Jump is taken (z = 1 or s != o)"
        else:
            output_string="Jump is NOT taken (z != 1 or s = o)"
    ## opcode 0x75: JNE, JNZ (jump if ZF = 0)
    ## opcode 0x0F85: JNE, JNZ (jump if ZF = 0)
    elif "jne" == mnemonic or "jnz" == mnemonic:
        if flags["ZF"] == 0:
            output_string="Jump is taken (z = 0)"
        else:
            output_string="Jump is NOT taken (z != 0)"
    ## opcode 0x71: JNO (OF = 0)
    ## opcode 0x0F81: JNO (OF = 0)
    elif "jno" == mnemonic:
        if flags["OF"] == 0:
            output_string="Jump is taken (o = 0)"
        else:
            output_string="Jump is NOT taken (o != 0)"
    ## opcode 0x7B: JNP, JPO (jump if PF = 0)
    ## opcode 0x0F8B: JPO (jump if PF = 0)
    elif "jnp" == mnemonic or "jpo" == mnemonic:
        if flags["PF"] == 0:
            output_string="Jump is NOT taken (p = 0)"
        else:
            output_string="Jump is taken (p != 0)"
    ## opcode 0x79: JNS (jump if SF = 0)
    ## opcode 0x0F89: JNS (jump if SF = 0)
    elif "jns" == mnemonic:
        if flags["SF"] == 0:
            output_string="Jump is taken (s = 0)"
        else:
            output_string="Jump is NOT taken (s != 0)"
    ## opcode 0x70: JO (jump if OF=1)
    ## opcode 0x0F80: JO (jump if OF=1)
    elif "jo" == mnemonic:
        if flags["OF"] == 1:
            output_string="Jump is taken (o = 1)"
        else:
            output_string="Jump is NOT taken (o != 1)"
    ## opcode 0x7A: JP, JPE (jump if PF=1)
    ## opcode 0x0F8A: JP, JPE (jump if PF=1)
    elif "jp" == mnemonic or "jpe" == mnemonic:
        if flags["PF"] == 1:
            output_string="Jump is taken (p = 1)"
        else:
            output_string="Jump is NOT taken (p != 1)"
    ## opcode 0x78: JS (jump if SF=1)
    ## opcode 0x0F88: JS (jump if SF=1)
    elif "js" == mnemonic:
        if flags["SF"] == 1:
            output_string="Jump is taken (s = 1)"
        else:
            output_string="Jump is NOT taken (s != 1)"

    if output_string:
        if is_i386():
            output(" " + output_string)
        elif is_x64():
            output(" "*46 + output_string)
        else:
            output(output_string)

    color("RESET")

def dump_cpsr(cpsr):
    # XXX: some fields reserved in recent ARM specs so we should revise and set to latest?
    cpsrTuples = [ ('N', 31), ('Z', 30), ('C', 29), ('V', 28), ('Q', 27), ('J', 24), 
                   ('E', 9), ('A', 8), ('I', 7), ('F', 6), ('T', 5) ]
    # use the first character of each register key to output, lowercase if bit not set
    for flag, bitfield in cpsrTuples :
        if bool(cpsr & (1 << bitfield)) == True:
            output(flag + " ")
        else:
            output(flag.lower() + " ")

def dump_jump_arm64(cpsr):
    masks = { 'N': 31, 'Z':30, 'C':29, 'V': 28, 'Q':27, 'J':24, 'E':9, 'A':8, 'I':7, 'F':6, 'T':5}
    flags = { key: bool(cpsr & (1 << value)) for key, value in masks.items() }

    if is_aarch64():
        pc_addr = get_gp_register("pc")
    else:
        print("[-] dump_jump_arm64() error: wrong architecture.")
        return

    mnemonic = get_mnemonic(pc_addr)
    color("RED")
    output_string=''

    if mnemonic == 'cbnz' or mnemonic == 'tbnz':
        if not flags['Z']:
            output_string = "Jump is taken (Z = 0)"
        else:
            output_string = "Jump is NOT taken (Z = 1)"
    
    elif mnemonic == 'cbz' or mnemonic == 'tbz':
        if flags['Z']:
            output_string = "Jump is taken (Z = 1)"
        else:
            output_string = "Jump is NOT taken (Z = 0)"
    
    elif mnemonic == 'b.eq':
        if flags['Z']:
            output_string = "Jump is taken (Z = 1)"
        else:
            output_string = "Jump is NOT taken (Z = 0)"
    
    elif mnemonic == 'b.ne':
        if not flags['Z']:
            output_string = "Jump is taken (Z = 0)"
        else:
            output_string = "Jump is NOT taken (Z = 1)"
    
    elif mnemonic == 'b.cs' or mnemonic == 'b.hs':
        if flags['C']:
            output_string = "Jump is taken (C = 1)"
        else:
            output_string = "Jump is NOT taken (C = 0)"
    
    elif mnemonic == 'b.cc' or mnemonic == 'b.lo':
        if not flags['C']:
            output_string = "Jump is taken (C = 0)"
        else:
            output_string = "Jump is NOT taken (C = 1)"
    
    elif mnemonic == 'b.mi':
        if flags['N']:
            output_string = "Jump is taken (N = 1)"
        else:
            output_string = "Jump is NOT taken (N = 0)"
    
    elif mnemonic in ('csel', 'csinc', 'csinv', 'csneg'):
        operands = get_operands(pc_addr)
        if flags['Z']:
            output_string = mnemonic + " => " + operands.split(',')[1]
        else:
            output_string = mnemonic + " => " + operands.split(',')[2]
    
    elif mnemonic in ('cset', 'csetm'):
        operands = get_operands(pc_addr)

        if flags['Z']:
            result = 1 if mnemonic == 'cset' else -1
        else:
            result = 0
        output_string = "{0} => {1} = {2}".format(mnemonic, operands.split(',')[0], result)
    
    if output_string:
        output(' '*40 + output_string)
    
    color("RESET")

def print_registers():
    if is_i386(): 
        # reg32()
        register_format = x86_registers
    elif is_x64():
        # reg64()
        register_format = x86_64_registers
    elif is_arm():
        # regarm()
        register_format = arm_32_registers
    elif is_aarch64():
        register_format = aarch64_registers
    else:
        raise OSError('Unsupported Architecture')

    print_cpu_registers(register_format)

def HandleHookStopOnTarget(debugger, command, result, dict):
    '''Display current code context.'''
    # Don't display anything if we're inside Xcode
    if os.getenv('PATH').startswith('/Applications/Xcode.app'):
        return
    
    global GlobalListOutput
    global CONFIG_DISPLAY_STACK_WINDOW
    global CONFIG_DISPLAY_FLOW_WINDOW
    global CONFIG_NO_CTX

    if CONFIG_NO_CTX:
        return 0

    debugger.SetAsync(True)

    # when we start the thread is still not valid and get_frame() will always generate a warning
    # this way we avoid displaying it in this particular case
    if get_process().GetNumThreads() == 1:
        thread = get_process().GetThreadAtIndex(0)
        if thread.IsValid() == False:
            return

    # frame = get_frame()
    # frame = get_frame_at_selected_thread()
    # if type(frame) == type(None): 
    #   return
            
    # thread= frame.GetThread()
    while True:
        frame = get_frame()
        if type(frame) == type(None): 
            return

        thread = frame.GetThread()
        if thread.GetStopReason() == lldb.eStopReasonNone or thread.GetStopReason() == lldb.eStopReasonInvalid:
        # if thread.GetStopReason() == lldb.eStopReasonInvalid:
            time.sleep(0.001)
        else:
            break
        
    GlobalListOutput = []
    
    arch = get_arch()
    if not is_i386() and not is_x64() and not is_arm() and not is_aarch64():
        #this is for ARM probably in the future... when I will need it...
        print("[-] error: Unknown architecture : " + arch)
        return

    color(COLOR_SEPARATOR)
    if is_i386() or is_arm():
        output("---------------------------------------------------------------------------------")
    elif is_x64() or is_aarch64():
        output("-----------------------------------------------------------------------------------------------------------------------")
            
    color("BOLD")
    output("[regs]\n")
    color("RESET")
    print_registers()

    if CONFIG_DISPLAY_STACK_WINDOW == 1:
        color(COLOR_SEPARATOR)
        if is_i386() or is_arm():
            output("--------------------------------------------------------------------------------")
        elif is_x64() or is_aarch64():
            output("----------------------------------------------------------------------------------------------------------------------")
        color("BOLD")
        output("[stack]\n")
        color("RESET")
        display_stack()
        output("\n")

    if CONFIG_DISPLAY_DATA_WINDOW == 1:
        color(COLOR_SEPARATOR)
        if is_i386() or is_arm():
            output("---------------------------------------------------------------------------------")
        elif is_x64() or is_aarch64():
            output("-----------------------------------------------------------------------------------------------------------------------")
        color("BOLD")
        output("[data]\n")
        color("RESET")
        display_data()
        output("\n")

    if CONFIG_DISPLAY_FLOW_WINDOW == 1 and is_x64() and is_aarch64():
        color(COLOR_SEPARATOR)
        if is_i386() or is_arm():
            output("---------------------------------------------------------------------------------")
        elif is_x64() or is_aarch64():
            output("-----------------------------------------------------------------------------------------------------------------------")
        color("BOLD")
        output("[flow]\n")
        color("RESET")
        display_indirect_flow()

    color(COLOR_SEPARATOR)
    if is_i386() or is_arm():
        output("---------------------------------------------------------------------------------")
    elif is_x64() or is_aarch64():
        output("-----------------------------------------------------------------------------------------------------------------------")
    color("BOLD")
    output("[code]\n")
    color("RESET")
            
    # disassemble and add its contents to output inside
    disassemble(get_current_pc(), CONFIG_DISASSEMBLY_LINE_COUNT)
        
    color(COLOR_SEPARATOR)
    if get_pointer_size() == 4: #is_i386() or is_arm():
        output("---------------------------------------------------------------------------------------")
    elif get_pointer_size() == 8: #is_x64():
        output("-----------------------------------------------------------------------------------------------------------------------------")
    color("RESET")
    
    # XXX: do we really need to output all data into the array and then print it in a single go? faster to just print directly?
    # was it done this way because previously disassembly was capturing the output and modifying it?
    data = "".join(GlobalListOutput)
    result.PutCString(data)
    result.SetStatus(lldb.eReturnStatusSuccessFinishResult)
    return 0
