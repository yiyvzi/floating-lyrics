Option Explicit

Dim files, shell, project, python, entry, result
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
project = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(project, ".venv\Scripts\pythonw.exe")
entry = files.BuildPath(project, "main.py")

If Not files.FileExists(python) Then
    MsgBox "Run start.bat once to set up Floating Lyrics, then use start.vbs.", vbInformation, "Floating Lyrics"
    WScript.Quit 1
End If

shell.CurrentDirectory = project
On Error Resume Next
result = shell.Run("""" & python & """ """ & entry & """", 1, True)
If Err.Number <> 0 Or result <> 0 Then
    MsgBox "Floating Lyrics could not start. Run start.bat to see the error.", vbExclamation, "Floating Lyrics"
    WScript.Quit 1
End If
