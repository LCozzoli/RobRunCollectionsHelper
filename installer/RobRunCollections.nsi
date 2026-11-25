; Collections Helper installer (NSIS 3, Unicode, 13 languages picked from Windows).
; Per-user install, no administrator rights: the app writes its settings (settings.json), log and last
; result next to its executable.
; Build the executable first (build.ps1), then: makensis -DVERSION=1.2.0 installer\RobRunCollections.nsi

Unicode true
Target x86-unicode  ; official NSIS builds only ship x86 stubs; the 32-bit installer runs on any Windows
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

!ifndef VERSION
  !define VERSION "1.0.0"
!endif
!define APPNAME "Collections Helper"
!define OLDNAME "ROB RUN Collections"
!define EXENAME "RobRunCollections.exe"
!define WINDOWTITLE "Collections Helper"
!define OLDTITLE2 "Collections Helper by SegFault"
!define OLDTITLE "ROB RUN · Collections"
!define REGKEY "Software\RobRunCollections"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\RobRunCollections"

Name "${APPNAME}"
Caption "${APPNAME} ${VERSION}"
OutFile "..\dist\RobRunCollections-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\RobRunCollections"
InstallDirRegKey HKCU "${REGKEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${APPNAME} ${VERSION}"
ShowInstDetails nevershow
ShowUninstDetails nevershow

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1036 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=1036 "FileDescription" "${APPNAME} setup"
VIAddVersionKey /LANG=1036 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1036 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1036 "CompanyName" "SegFault"
VIAddVersionKey /LANG=1036 "LegalCopyright" "GPL-3.0 · unofficial fan tool for ROB RUN"

!define MUI_ICON "..\src\data\app.ico"
!define MUI_UNICON "..\src\data\app.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "$(WelcomeTitle)"
!define MUI_WELCOMEPAGE_TEXT "$(WelcomeText)"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXENAME}"
!define MUI_FINISHPAGE_RUN_TEXT "$(RunText)"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\README.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "$(ReadmeText)"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!include "langs.nsh"  ; MUI languages + translated texts; Windows language chosen automatically

; Closes an open app window cleanly (it saves its settings on close).
!macro CloseRunningApp UN
Function ${UN}CloseRunningApp
  FindWindow $0 "TkTopLevel" "${WINDOWTITLE}"
  ${If} $0 = 0
    FindWindow $0 "TkTopLevel" "${OLDTITLE}"
  ${EndIf}
  ${If} $0 = 0
    FindWindow $0 "TkTopLevel" "${OLDTITLE2}"
  ${EndIf}
  ${If} $0 = 0
    Return
  ${EndIf}
  IfSilent close
  MessageBox MB_OKCANCEL|MB_ICONINFORMATION "$(AppOpen)" IDOK close
  Abort
  close:
  StrCpy $1 0
  ${Do}
    FindWindow $0 "TkTopLevel" "${WINDOWTITLE}"
    ${If} $0 = 0
      FindWindow $0 "TkTopLevel" "${OLDTITLE}"
    ${EndIf}
    ${If} $0 = 0
      FindWindow $0 "TkTopLevel" "${OLDTITLE2}"
    ${EndIf}
    ${If} $0 = 0
      ${Break}
    ${EndIf}
    SendMessage $0 ${WM_CLOSE} 0 0
    Sleep 250
    IntOp $1 $1 + 1
  ${LoopUntil} $1 >= 40
  Sleep 500
FunctionEnd
!macroend
!insertmacro CloseRunningApp ""
!insertmacro CloseRunningApp "un."

Section "$(SecAppName)" SecApp
  SectionIn RO
  Call CloseRunningApp
  SetOutPath "$INSTDIR"
  SetOverwrite on
  File "..\dist\${EXENAME}"
  File "/oname=README.txt" "..\docs\USER_GUIDE.txt"
  ; Files of earlier versions
  Delete "$INSTDIR\LIRE-MOI.txt"
  Delete "$INSTDIR\Desinstaller.exe"
  Delete "$INSTDIR\dernier-resultat.json"

  ; Shortcuts of the former name (1.0.x)
  Delete "$SMPROGRAMS\${OLDNAME}.lnk"
  Delete "$DESKTOP\${OLDNAME}.lnk"
  CreateShortcut "$SMPROGRAMS\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\${EXENAME}" 0 \
    SW_SHOWNORMAL "" "$(ShortcutComment)"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${REGKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "Version" "${VERSION}"
  WriteRegStr HKCU "${REGKEY}" "Language" "$LANGUAGE"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "SegFault"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\${EXENAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINSTKEY}" "EstimatedSize" "$0"
SectionEnd

Section "$(SecDesktopName)" SecDesktop
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\${EXENAME}" 0 \
    SW_SHOWNORMAL "" "$(ShortcutComment)"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecApp} "$(DescApp)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "$(DescDesktop)"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function un.onInit
  ReadRegStr $LANGUAGE HKCU "${REGKEY}" "Language"
FunctionEnd

Section "Uninstall"
  Call un.CloseRunningApp
  Delete "$INSTDIR\${EXENAME}"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\LIRE-MOI.txt"
  Delete "$INSTDIR\application.log"
  Delete "$INSTDIR\last-result.json"
  Delete "$INSTDIR\last-result.tmp"
  Delete "$INSTDIR\dernier-resultat.json"
  Delete "$INSTDIR\settings.tmp"
  ; Settings are kept by default (reinstall without setting everything up again).
  IfSilent keep
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "$(DeleteSettings)" IDNO keep
  Delete "$INSTDIR\settings.json"
  keep:
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${OLDNAME}.lnk"
  Delete "$DESKTOP\${OLDNAME}.lnk"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "${REGKEY}"
SectionEnd
