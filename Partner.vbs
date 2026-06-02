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
pythonwPath = "C:\Python314\pythonw.exe"
If Not objFSO.FileExists(pythonwPath) Then
    pythonwPath = objShell.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python\Python314\pythonw.exe"
End If
If Not objFSO.FileExists(pythonwPath) Then
    pythonwPath = "pythonw.exe"
End If

strCommand = """" & pythonwPath & """" & " -m partner.gui"
objShell.Run strCommand, 0, False
