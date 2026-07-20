Option Explicit

Dim shell, filesystem, scriptDirectory, syncScript, powershellPath
Dim command, argument, exitCode

Set shell = CreateObject("WScript.Shell")
Set filesystem = CreateObject("Scripting.FileSystemObject")

scriptDirectory = filesystem.GetParentFolderName(WScript.ScriptFullName)
syncScript = filesystem.BuildPath(scriptDirectory, "Sync-Portfolio.ps1")
powershellPath = shell.ExpandEnvironmentStrings( _
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")

command = Chr(34) & powershellPath & Chr(34) & _
    " -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " & _
    Chr(34) & syncScript & Chr(34) & " -Scheduled"

For Each argument In WScript.Arguments
    If LCase(CStr(argument)) = "-publish" Then
        command = command & " -Publish"
    End If
Next

' Window style 0 keeps the child process hidden; True preserves its exit code.
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
