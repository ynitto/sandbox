' Start-WslTerminals.vbs
'
' このファイルをダブルクリックすると wscript.exe (GUIホスト) 経由で
' PowerShell スクリプトをコンソールウィンドウなしに起動します。
'
' powershell.exe はコンソールアプリケーションのため、.ps1 を直接実行すると
' 必ずコンソールウィンドウが表示されます。VBScript を wscript.exe で実行する
' ことでウィンドウを完全に抑制できます。

Option Explicit

Dim oShell, scriptDir, psScript, psArgs

Set oShell = CreateObject("WScript.Shell")

' このVBSファイルと同じフォルダにある Start-WslTerminals.ps1 を起動する
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
psScript  = scriptDir & "Start-WslTerminals.ps1"

psArgs = "powershell.exe -NonInteractive -NoProfile -ExecutionPolicy Bypass" & _
         " -WindowStyle Hidden" & _
         " -File """ & psScript & """"

' 第2引数 0 = ウィンドウ非表示、第3引数 False = 非同期実行
oShell.Run psArgs, 0, False

Set oShell = Nothing
