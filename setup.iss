; ============================================================
; Inno Setup Script - Telecopy v3.0
; Com download automático do Visual C++ (x64 ou x86)
; ============================================================

#define MyAppName "Telecopy"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "montx2"
#define MyAppURL "https://github.com/montx2/telecopy"
#define MyAppExeName "Telecopy.exe"

[Setup]
AppId={{5CE879C7-A6B7-4459-806D-B4395A9EC935}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; IMPORTANTE: Defina aqui se seu app é x64 ou x86
; Se for x64 apenas: ArchitecturesAllowed=x64
; Se for x86 (32-bit): ArchitecturesAllowed=x86 
; Se quiser ambos: ArchitecturesAllowed=x86 x64 (mas precisa compilar duas vezes)
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

MinVersion=6.1sp1

PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline

DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
DisableDirPage=no
DisableWelcomePage=no
DisableFinishedPage=no

SetupIconFile=C:\Users\montx2\Desktop\Telecopy\telecopy.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} v{#MyAppVersion}
WizardStyle=modern
WizardSizePercent=100

OutputDir=C:\Users\montx2\Desktop\Telecopy\dist
; Nome do arquivo muda conforme arquitetura
OutputBaseFilename=Telecopy_Setup_v{#MyAppVersion}_{#SetupSetting("ArchitecturesAllowed")}
Compression=lzma2/ultra64
SolidCompression=yes

CloseApplications=force
CloseApplicationsFilter=*Telecopy.exe*
RestartApplications=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkablealone

[Files]
Source: "build\main.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM {#MyAppExeName} /T 2>nul"; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\*.tmp"
Type: files; Name: "{app}\*.log"

[Dirs]
Name: "{app}"; Permissions: users-full
Name: "{app}\data"; Permissions: users-full

[Code]
var
  DeleteDataPage: TInputOptionWizardPage;
  LanguagePage: TInputOptionWizardPage;
  DeleteData: Boolean;
  IsSystem64Bit: Boolean;
  VCDownloadURL: string;
  VCProductID: string;
  VCFileName: string;

// ============================================================
; FUNÇÕES DE DOWNLOAD E VERIFICAÇÃO
// ============================================================

#ifdef UNICODE
function DownloadTemporaryFile(const Url, FileName, Headers: string; ErrorCode: DWORD): Boolean;
  external 'DownloadTemporaryFileW@files:iscore.dll stdcall delayload';
#else
function DownloadTemporaryFile(const Url, FileName, Headers: string; ErrorCode: DWORD): Boolean;
  external 'DownloadTemporaryFileA@files:iscore.dll stdcall delayload';
#endif

function MsiQueryProductState(szProduct: string): Integer;
  external 'MsiQueryProductStateW@msi.dll stdcall';

function IsWow64: Boolean;
begin
  Result := ProcessorArchitecture = paX64;
end;

function VCVersionInstalled(const ProductID: string): Boolean;
begin
  Result := MsiQueryProductState(ProductID) = 5;
end;

// ============================================================
; CONFIGURAÇÃO DINÂMICA DO VC++ BASEADO NA ARQUITETURA
// ============================================================

procedure SetupVCConfig;
begin
  IsSystem64Bit := IsWin64;
  
  if IsSystem64Bit then
  begin
    // Windows 64-bit
    VCDownloadURL := 'https://aka.ms/vs/17/release/vc_redist.x64.exe';
    VCProductID := '{36F68A90-239C-34DF-B58C-64B30153CE35}'; // VC++ 2015-2022 x64
    VCFileName := 'vc_redist.x64.exe';
  end
  else
  begin
    // Windows 32-bit (x86)
    VCDownloadURL := 'https://aka.ms/vs/17/release/vc_redist.x86.exe';
    VCProductID := '{65E5BD06-6392-3027-8C26-853107B3C8D6}'; // VC++ 2015-2022 x86
    VCFileName := 'vc_redist.x86.exe';
  end;
end;

function VCRedistNeedsInstall: Boolean;
begin
  Result := not VCVersionInstalled(VCProductID);
end;

// ============================================================
; DOWNLOAD E INSTALAÇÃO DO VC++
// ============================================================

function InstallVCRedist: Boolean;
var
  TempFile: string;
  ErrorCode: Integer;
  DownloadResult: Boolean;
begin
  Result := false;
  TempFile := ExpandConstant('{tmp}\' + VCFileName);
  
  WizardForm.StatusLabel.Caption := 'Baixando Visual C++ Redistributable (' + VCFileName + ')...';
  WizardForm.StatusLabel.Visible := true;
  
  try
    DownloadResult := DownloadTemporaryFile(VCDownloadURL, VCFileName, '', ErrorCode);
    
    if not DownloadResult then
    begin
      MsgBox('Não foi possível baixar o Visual C++ Redistributable automaticamente.' + #13#10 +
             'Erro: ' + IntToStr(ErrorCode) + #13#10 + #13#10 +
             'Deseja abrir o link de download no navegador?', 
             mbError, MB_YESNO) = IDYES then
      begin
        ShellExec('open', VCDownloadURL, '', '', SW_SHOWNORMAL, ErrorCode);
      end;
      Result := false;
      exit;
    end;
    
    WizardForm.StatusLabel.Caption := 'Instalando Visual C++ Redistributable...';
    
    if Exec(TempFile, '/install /quiet /norestart', '', SW_SHOWNORMAL, ewWaitUntilTerminated, ErrorCode) then
    begin
      if (ErrorCode = 0) or (ErrorCode = 3010) or (ErrorCode = 1638) then
      begin
        Result := true;
      end
      else
      begin
        MsgBox('A instalação do Visual C++ retornou um erro (código: ' + IntToStr(ErrorCode) + ').' + #13#10 +
               'O aplicativo pode não funcionar corretamente.', mbError, MB_OK);
        Result := true; // Continua mesmo com erro, mas avisa
      end;
    end
    else
    begin
      MsgBox('Não foi possível executar o instalador do Visual C++.', mbError, MB_OK);
      Result := false;
    end;
    
  except
    ShellExec('open', VCDownloadURL, '', '', SW_SHOWNORMAL, ErrorCode);
    Result := false;
  end;
end;

// ============================================================
; PÁGINAS DO WIZARD
// ============================================================

procedure InitializeWizard;
begin
  // Configura qual VC++ usar baseado no sistema
  SetupVCConfig;
  
  // Verifica se o app é compatível com o sistema
  if (SetupSetting("ArchitecturesAllowed") = "x64") and not IsSystem64Bit then
  begin
    MsgBox('Este instalador é para sistemas 64-bit (x64).' + #13#10 + 
           'Seu sistema é 32-bit (x86).' + #13#10 + #13#10 +
           'Por favor, baixe a versão 32-bit do Telecopy.', mbError, MB_OK);
    Abort(); // Cancela instalação
  end;

  // Página de seleção de idioma
  LanguagePage := CreateInputOptionPage(wpSelectComponents,
    'Configuração de Idioma', 
    'Selecione o idioma padrão do aplicativo',
    'O Telecopy suporta Português (Brasil) e English (US).',
    True, False);
  
  LanguagePage.Add('Português (Brasil) - PT');
  LanguagePage.Add('English (US) - EN');
  LanguagePage.Values[0] := True;
  
  // Página de desinstalação
  DeleteDataPage := CreateInputOptionPage(wpConfirmUninstall,
    'Remover dados pessoais',
    'Deseja remover também seus dados e configurações?',
    'O {#MyAppName} armazena na pasta "data":' + #13#10 + 
    '• Suas credenciais criptografadas' + #13#10 + 
    '• Sessão do Telegram' + #13#10 + 
    '• Histórico de cópias e estatísticas' + #13#10 + 
    '• Arquivos de log',
    True, False);
  
  DeleteDataPage.Add('Manter meus dados (recomendado)');
  DeleteDataPage.Add('Remover todos os dados');
  DeleteDataPage.Values[0] := True;
end;

// ============================================================
; VERIFICAÇÃO APP EM EXECUÇÃO
// ============================================================

function IsAppRunning: Boolean;
var
  ErrorCode: Integer;
begin
  Result := not Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq {#MyAppExeName}" 2>nul | find /I "{#MyAppExeName}" >nul', '', SW_HIDE, ewWaitUntilTerminated, ErrorCode) or (ErrorCode <> 0);
end;

// ============================================================
; INICIALIZAÇÃO PRINCIPAL
// ============================================================

function InitializeSetup(): Boolean;
var
  ErrorCode: Integer;
  LocalVCPath: string;
begin
  Result := true;
  
  // Configura variáveis do VC++
  SetupVCConfig;
  
  // Verifica arquitetura compatível
  if (SetupSetting("ArchitecturesAllowed") = "x64") and not IsSystem64Bit then
  begin
    Result := false;
    exit; // Já mostrou mensagem no InitializeWizard
  end;
  
  // Fecha app se estiver rodando
  if IsAppRunning then
  begin
    if MsgBox('O {#MyAppName} está sendo executado.' + #13#10 + 
              'Deseja fechar automaticamente?', mbConfirmation, MB_YESNO) = IDYES then
    begin
      Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM {#MyAppExeName} /T 2>nul', '', SW_HIDE, ewWaitUntilTerminated, ErrorCode);
      Sleep(2000);
      
      if IsAppRunning then
      begin
        MsgBox('Não foi possível fechar o programa. Feche manualmente.', mbError, MB_OK);
        Result := false;
        exit;
      end;
    end else
    begin
      Result := false;
      exit;
    end;
  end;
  
  // Instala VC++ se necessário
  if VCRedistNeedsInstall then
  begin
    // Tenta usar arquivo local primeiro
    LocalVCPath := ExpandConstant('{src}\' + VCFileName);
    if FileExists(LocalVCPath) then
    begin
      WizardForm.StatusLabel.Caption := 'Instalando Visual C++ (offline)...';
      if Exec(LocalVCPath, '/install /quiet /norestart', '', SW_SHOWNORMAL, ewWaitUntilTerminated, ErrorCode) then
      begin
        Result := true;
        exit;
      end;
    end;
    
    // Se não tem local, baixa da internet
    if not InstallVCRedist then
    begin
      Result := false;
      exit;
    end;
  end;
end;

// ============================================================
; NAVEGAÇÃO
// ============================================================

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir: string;
  ExitCode: Integer;
begin
  Result := true;
  
  if CurPageID = wpSelectDir then
  begin
    Dir := WizardDirValue;
    
    if (Pos('\Program Files', Dir) <> 0) or 
       (Pos('\Arquivos de Programas', Dir) <> 0) or
       (Pos(':\Windows', Dir) = 1) then
    begin
      if not IsAdminLoggedOn then
      begin
        if MsgBox('Esta pasta requer privilégios de administrador.' + #13#10 + 
                  'Deseja reiniciar como Administrador?', mbConfirmation, MB_YESNO) = IDYES then
        begin
          ShellExecute('runas', ExpandConstant('{srcexe}'), 
                      ExpandConstant('/SP- /DIR="' + Dir + '"'), '', SW_SHOWNORMAL, ExitCode);
          Result := false;
          WizardForm.Close;
        end else
        begin
          Result := false;
        end;
      end;
    end;
  end;
  
  if CurPageID = DeleteDataPage.ID then
    DeleteData := DeleteDataPage.Values[1];
end;

// ============================================================
; PÓS-INSTALAÇÃO
// ============================================================

procedure CurStepChanged(CurStep: TSetupStep);
var
  LangCode: string;
  ConfigPath: string;
begin
  if CurStep = ssPostInstall then
  begin
    if LanguagePage.Values[0] then
      LangCode := 'pt'
    else
      LangCode := 'en';
    
    ConfigPath := ExpandConstant('{app}\data');
    ForceDirectories(ConfigPath);
    
    SaveStringToFile(ConfigPath + '\config.json',
      '{' + #13#10 +
      '  "language": "' + LangCode + '",' + #13#10 +
      '  "arch": "' + SetupSetting("ArchitecturesAllowed") + '",' + #13#10 +
      '  "version": "3.0.0"' + #13#10 +
      '}', False);
  end;
end;

// ============================================================
; DESINSTALAÇÃO
// ============================================================

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DeleteData then
  begin
    if DirExists(ExpandConstant('{app}\data')) then
      DelTree(ExpandConstant('{app}\data'), True, True, True);
  end;
end;