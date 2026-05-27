Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Get the directory of this script
strScriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)

' Set working directory
objShell.CurrentDirectory = strScriptDir

' Set environment
objShell.Environment("Process")("PYTHONPATH") = strScriptDir & ";" & objShell.Environment("Process")("PYTHONPATH")
objShell.Environment("Process")("PYTHONIOENCODING") = "utf-8"
objShell.Environment("Process")("PYTHONUTF8") = "1"

' Launch GUI with pythonw (no terminal window)
strCommand = "pythonw.exe -m partner.gui"
objShell.Run strCommand, 0, False
