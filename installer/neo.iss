; Inno Setup 6. Сборка: iscc /DAppVersion=0.1.0 installer\neo.iss
; Необязательно: /DBundleOllama  — положить Ollama прямо в установщик (папка build\ollama).
; Внимание: файл в GitHub Release не может быть больше 2 ГиБ.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B7A6F3C2-5E4D-4A1B-9C7E-2D8F0A1B3C4D}
AppName=Neo AI
AppVersion={#AppVersion}
AppPublisher=Norton-Ognev
DefaultDirName={autopf}\NeoAI
DefaultGroupName=Neo AI
OutputDir=..\Output
OutputBaseFilename=NeoAI-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\NeoAI.exe
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"

[Files]
Source: "..\dist\NeoAI\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
#ifdef BundleOllama
Source: "..\build\ollama\*"; DestDir: "{app}\ollama"; Flags: recursesubdirs ignoreversion
#endif

[Icons]
Name: "{group}\Neo AI"; Filename: "{app}\NeoAI.exe"
Name: "{autodesktop}\Neo AI"; Filename: "{app}\NeoAI.exe"; Tasks: desktopicon

[Run]
; При первом запуске приложение само покажет окно настройки и докачает модели.
Filename: "{app}\NeoAI.exe"; Description: "Запустить Neo и скачать модели (около 7 ГБ)"; Flags: postinstall nowait skipifsilent

[Code]
// pywebview на Windows использует Microsoft Edge WebView2 Runtime.
function WebView2Installed: Boolean;
var v: String;
begin
  Result := RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v)
         or RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v);
  Result := Result and (v <> '') and (v <> '0.0.0.0');
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if not WebView2Installed then
    MsgBox('Не найден Microsoft Edge WebView2 Runtime (нужен для окна Neo). ' +
           'На Windows 11 он есть по умолчанию; на Windows 10 скачайте его с microsoft.com/edge/webview2 ' +
           'и запустите установку Neo снова, если окно не откроется.', mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var base: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    base := ExpandConstant('{localappdata}\Neo');
    if MsgBox('Удалить скачанные модели (несколько ГБ)? События и настройки Neo останутся.',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      DelTree(base + '\ollama', True, True, True);
      DelTree(base + '\runtime', True, True, True);
      DelTree(base + '\hf', True, True, True);
      DelTree(base + '\torch', True, True, True);
      DelTree(base + '\models', True, True, True);
      DeleteFile(base + '\setup_state.json');
    end;
  end;
end;
