using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace LocalVideoStudio {
internal sealed class StudioForm : Form {
 [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr hwnd);
 internal bool Dark=true;
 internal float UiScale=1;
 internal int Page;
 internal static readonly int[] VideoDurations={3,4,5,6,8,10,12,15,20,30};
 internal static readonly int[] SizeW={768,1280,448,704,512,1024,1920,1088,2560,1440,3840,2176};
 internal static readonly int[] SizeH={448,704,768,1280,512,1024,1088,1920,1440,2560,2176,3840};
 internal readonly bool Testing;
 readonly List<Control> creation=new List<Control>(),connections=new List<Control>(),library=new List<Control>(),bound=new List<Control>();
 readonly Dictionary<Control,Rectangle> positions=new Dictionary<Control,Rectangle>();
 readonly Dictionary<Control,float> fontSizes=new Dictionary<Control,float>();
 readonly Dictionary<string,Font> fonts=new Dictionary<string,Font>();
 readonly List<QuietButton> buttons=new List<QuietButton>();
 readonly Timer poll=new Timer{Interval=3500},saveDraft=new Timer{Interval=700};
 readonly List<Shot> shots;
 QuietButton navCreate,navConnect,navLibrary,theme,run,unload,improve,improveContent,saveKey,testKey,removeKey,addImage,addVideo,clearVideo,addAudio,clearAudio,pauseTask,cancelTask;
 QuietButton imgRun,ttsRun,ttsPlay,ttsOpen,lipGoto,dramaRun,dramaApply;
 RefThumb[] imageThumbs=new RefThumb[6];
 QuietButton[] imageRemoves=new QuietButton[6];
 RefThumb videoThumb;
 ChoiceBox agent,preset,size,duration,imgRatio,voiceChoice,rateChoice,shotChoice,dramaRatio,dramaCount,computePath;
 readonly ToolTip computePathTip=new ToolTip{AutoPopDelay=18000,InitialDelay=350,ReshowDelay=150};
 ToggleSwitch fastSwitch,lightningSwitch;
 TextBox prompt,content,seed,key,baseUrl,apiModel,instruct,imgPrompt,imgSeed,ttsText,dramaStory;
 Label connectionDetail,agentNote,modelNote,keyState,globalState,notice,jobDetail,libraryNote,gpuState,gpuNote,progressTitle,fastLabel,audioNote,instructLabel,imgNote,lipNote,dramaNote;
 GenerationMeter generationMeter;
 ListBox outputList,imgList,ttsList,dramaList;
 MediaPreview preview,libraryPreview,imgPreview;
 bool polling,working,settingsLoaded,populating,connected,dirty,closing,showingBound,keySaved,editingKey,submittingVideo;
 int settingsRevision,shotIndex;
 readonly List<KeyValuePair<string,int>> dramaShotsData=new List<KeyValuePair<string,int>>();
 string savedKimiModel="",savedKimiUrl="";
 readonly Func<string,object,int,Dictionary<string,object>> connectionRequest;
 QuietButton boundBack,boundTest,boundChange,boundRemove;
 Label boundModel,boundUrl,boundCheckState;
 string noticeText="描述画面，可添加参考图像或参考视频，再交接出片。",lastError="";
 object controller;
 Dictionary<string,object> latest=new Dictionary<string,object>();
 readonly string themeFile=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"appearance.json");
 Color Bg{get{return Dark?StudioPalette.Background:Color.FromArgb(247,248,246);}}
 Color Card{get{return Dark?StudioPalette.Panel:Color.White;}}
 Color Ink{get{return Dark?StudioPalette.Ink:Color.FromArgb(29,36,31);}}
 Color Muted{get{return Dark?StudioPalette.Muted:Color.FromArgb(115,125,117);}}
 Color Line{get{return Dark?StudioPalette.Line:Color.FromArgb(222,228,222);}}
 Color Accent{get{return Dark?StudioPalette.Gold:Color.FromArgb(196,110,84);}}
 Shot Current {get{return shots[Math.Min(shotIndex,shots.Count-1)];}}
 internal StudioForm(bool testing=false,Func<string,object,int,Dictionary<string,object>> request=null) {
  connectionRequest=request??new Func<string,object,int,Dictionary<string,object>>(Api.Request);
  Testing=testing;AutoScaleMode=AutoScaleMode.None;DoubleBuffered=true;Text="视频工作台";Font=UiFont(13);StartPosition=FormStartPosition.CenterScreen;FormBorderStyle=FormBorderStyle.FixedSingle;MaximizeBox=false;
  try{Icon=new Icon(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"studio.ico"));}catch{}
  if(!Testing)try{if(File.Exists(themeFile))Dark=Json.Yes(Json.Serializer.DeserializeObject(File.ReadAllText(themeFile)),"dark");}catch{}
  shots=Testing?new List<Shot>{Shot.Create(1)}:DraftStore.Load();
  navCreate=ButtonAt("✎",12,96,32,32,null,delegate{ShowPage(0);});navCreate.AccessibleName="创作";
  navConnect=ButtonAt("⇆",12,152,32,32,null,delegate{ShowPage(1);});navConnect.AccessibleName="连接";
  navLibrary=ButtonAt("▤",12,208,32,32,null,delegate{ShowPage(2);});navLibrary.AccessibleName="成片";
  theme=ButtonAt("◐",12,856,32,32,null,delegate{SetTheme(!Dark,true);});theme.AccessibleName="切换主题";
  globalState=LabelAt("连接本地服务…",1530,32,362,24,9,null);globalState.TextAlign=ContentAlignment.MiddleRight;
  BuildCreation();BuildConnections();BuildBound();BuildLibrary();
  notice=LabelAt(noticeText,76,880,1500,16,9,null);
  poll.Tick+=async delegate{await RefreshStatus();};
  saveDraft.Tick+=delegate{saveDraft.Stop();SaveDraft();};
  Shown+=async delegate{uint dpi=GetDpiForWindow(Handle);LayoutScale(FitScale(dpi));var area=Screen.FromHandle(Handle).WorkingArea;Location=new Point(area.X+Math.Max(0,(area.Width-Width)/2),area.Y+Math.Max(0,(area.Height-Height)/2));SetTheme(Dark,false);RefreshVoiceFiles();var refreshImages=RefreshImageFiles();var loadVoices=LoadVoices();if(!Testing){await RefreshStatus();if(closing||IsDisposed)return;poll.Start();if(DraftStore.LastLoadWarning.Length>0)SetNotice(DraftStore.LastLoadWarning,true);}};
  FormClosing+=delegate{closing=true;saveDraft.Stop();SaveDraft();};
  FormClosed+=delegate{poll.Stop();poll.Dispose();saveDraft.Dispose();computePathTip.Dispose();};
  LoadShot();SetTheme(Dark,false);LayoutScale(1);ShowPage(0);SetControls();
 }
 QuietButton ButtonAt(string text,int x,int y,int w,int h,List<Control> page,EventHandler click,bool primary=false){var b=new QuietButton{Text=text,Primary=primary};b.Click+=click;buttons.Add(b);Add(b,x,y,w,h,9.5f,page);return b;}
 Label LabelAt(string text,int x,int y,int w,int h,float fs,List<Control> page){var l=new Label{Text=text,AutoSize=false,AutoEllipsis=true,BackColor=Color.Transparent};Add(l,x,y,w,h,fs,page);return l;}
 TextBox TextAt(int x,int y,int w,int h,List<Control> page,bool multi=false){var t=new TextBox{BorderStyle=BorderStyle.None,Multiline=multi,AcceptsReturn=multi,ScrollBars=multi?ScrollBars.Vertical:ScrollBars.None};Add(t,x,y,w,h,10.5f,page);return t;}
 ChoiceBox ComboAt(int x,int y,int w,List<Control> page){var c=new ChoiceBox();Add(c,x,y,w,34,9.5f,page);return c;}
 RefThumb ThumbAt(int x,int y,List<Control> page){var t=new RefThumb();Add(t,x,y,64,42,9,page);return t;}
 void Add(Control c,int x,int y,int w,int h,float fs,List<Control> page){positions[c]=new Rectangle(x,y,w,h);fontSizes[c]=fs;Controls.Add(c);if(page!=null)page.Add(c);if(c is TextBox){c.Enter+=delegate{Invalidate();};c.Leave+=delegate{Invalidate();};c.HandleCreated+=delegate{ApplyNativeTheme(c);};}}
 void BuildCreation(){BuildDramakePane();BuildImagePane();BuildVideoPane();BuildVoicePane();}
 void BuildDramakePane(){
  LabelAt("Dramake · 短剧",76,116,300,26,13,creation);
  LabelAt("故事 / 点子",76,152,200,18,9,creation);
  dramaStory=TextAt(76,174,438,110,creation,true);dramaStory.MaxLength=6000;dramaStory.AccessibleName="短剧故事或点子";
  LabelAt("画幅",76,292,100,18,9,creation);dramaRatio=ComboAt(76,314,140,creation);dramaRatio.Items.AddRange(new object[]{"竖屏 9:16","横屏 16:9","方形 1:1"});dramaRatio.SelectedIndex=0;
  LabelAt("分镜数",232,292,100,18,9,creation);dramaCount=ComboAt(232,314,90,creation);dramaCount.Items.AddRange(new object[]{new Choice("4","4 镜"),new Choice("6","6 镜"),new Choice("8","8 镜"),new Choice("10","10 镜"),new Choice("12","12 镜")});dramaCount.SelectedIndex=1;
  dramaRun=ButtonAt("✦ 生成分镜",334,308,180,42,creation,async delegate{await DramakePlan();},true);
  dramaNote=LabelAt("由视频区所选 Agent 生成剧本与分镜",76,368,438,18,8.5f,creation);
  LabelAt("分镜结果",76,396,200,18,9,creation);
  dramaList=new ListBox{BorderStyle=BorderStyle.None,IntegralHeight=false,DrawMode=DrawMode.OwnerDrawVariable};Add(dramaList,76,418,438,344,9,creation);dramaList.DrawItem+=DrawDramaItem;dramaList.MeasureItem+=MeasureDramaItem;
  dramaApply=ButtonAt("加入分镜 →",76,774,180,36,creation,delegate{DramakeApply();});
  LabelAt("加入后在视频区顶部切换镜头、逐镜交接出片",76,822,438,30,8.5f,creation);
 }
 void BuildImagePane(){
  LabelAt("文生图 · Qwen-Image",534,116,300,26,13,creation);
  LabelAt("出图描述",534,152,200,18,9,creation);
  imgPrompt=TextAt(534,174,438,96,creation,true);imgPrompt.MaxLength=8000;imgPrompt.AccessibleName="出图描述";
  LabelAt("分辨率",534,278,150,18,9,creation);imgRatio=ComboAt(534,300,150,creation);imgRatio.Items.AddRange(new object[]{"方形 1328×1328","横屏 1664×928","竖屏 928×1664"});imgRatio.SelectedIndex=0;
  LabelAt("随机种子",696,278,100,18,9,creation);imgSeed=TextAt(696,300,90,32,creation);imgSeed.Text="42";imgSeed.AccessibleName="出图随机种子";
  LabelAt("闪电8步",796,278,64,18,9,creation);lightningSwitch=new ToggleSwitch();Add(lightningSwitch,796,300,52,28,9,creation);lightningSwitch.Set(true);
  imgRun=ButtonAt("生成图片",858,294,114,42,creation,async delegate{await PrepareImage();},true);
  imgNote=LabelAt("图像模型状态待检查",534,352,438,18,8.5f,creation);
  imgPreview=new MediaPreview();Add(imgPreview,534,378,438,380,10,creation);imgPreview.StatusChanged+=delegate(string text){SetNotice(text,false);};imgPreview.Error+=delegate(string text){SetNotice(text,true);};
  LabelAt("最近出图",534,768,200,18,9,creation);
  imgList=new ListBox{BorderStyle=BorderStyle.None,IntegralHeight=false,HorizontalScrollbar=true,DrawMode=DrawMode.OwnerDrawFixed};Add(imgList,534,790,438,80,9,creation);imgList.DrawItem+=DrawImageItem;imgList.SelectedIndexChanged+=delegate{var p=imgList.SelectedItem as string;if(p!=null&&File.Exists(p))imgPreview.ShowImage(p);};
 }
 void BuildVideoPane(){
  LabelAt("视频生成",992,116,400,26,13,creation);
  LabelAt("镜头",1588,120,50,18,9,creation);shotChoice=ComboAt(1640,116,228,creation);shotChoice.SelectedIndexChanged+=delegate{SwitchShot();};
  LabelAt("Agent（大模型）",992,150,150,18,9,creation);
  agent=ComboAt(992,172,420,creation);agent.Items.AddRange(new object[]{new Choice("kimi_api","Kimi API"),new Choice("codex","Codex"),new Choice("qwen","Qwen Agent"),new Choice("kimi_code","Kimi Code")});agent.SelectedIndex=0;
  agent.SelectedIndexChanged+=async delegate{UpdateAgentNote();if(settingsLoaded&&!Testing)await PersistAgent();};
  agentNote=LabelAt("Kimi 协助构思，视频由本地模型生成。",992,208,560,26,8.5f,creation);
  preview=new MediaPreview();Add(preview,992,240,560,262,10,creation);preview.StatusChanged+=delegate(string text){SetNotice(text,false);};preview.Error+=delegate(string text){SetNotice(text,true);};
  LabelAt("内容（本次视频）",1568,150,150,18,9,creation);
  improveContent=ButtonAt("✦ 优化内容",1744,146,124,26,creation,async delegate{await ImproveBox(content,"内容");});
  content=TextAt(1568,172,300,330,creation,true);content.MaxLength=4000;content.AccessibleName="本次生成的视频内容";content.TextChanged+=delegate{EditCurrent();};
  LabelAt("提示词（先设置，作为风格前缀）",992,512,320,18,9,creation);
  improve=ButtonAt("✦ 优化提示词",1744,508,124,26,creation,async delegate{await ImproveBox(prompt,"提示词");});
  prompt=TextAt(992,534,876,58,creation,true);prompt.MaxLength=12000;prompt.AccessibleName="视频画面描述";prompt.TextChanged+=delegate{EditCurrent();};
  LabelAt("参考图像",992,602,120,18,9,creation);
  var imageHint=LabelAt("最多 6 张",1200,602,86,18,8f,creation);imageHint.TextAlign=ContentAlignment.MiddleRight;
  for(int i=0;i<6;i++){int slot=i;var t=new RefThumb();Add(t,992+slot*46,624,40,40,9,creation);imageThumbs[slot]=t;t.Click+=delegate{var paths=Current.ImagePaths;if(slot<paths.Count&&File.Exists(paths[slot])&&preview!=null)preview.ShowImage(paths[slot]);};imageRemoves[slot]=ButtonAt("×",992+slot*46+24,620,16,16,creation,delegate{RemoveImageAt(slot);});Controls.SetChildIndex(imageRemoves[slot],0);}
  addImage=ButtonAt("＋",992,624,40,40,creation,delegate{ChooseImage();});addImage.AccessibleName="添加参考图像：最多 6 张，用法随模型";
  LabelAt("参考视频",1300,602,120,18,9,creation);
  videoThumb=ThumbAt(1300,624,creation);videoThumb.Click+=delegate{ChooseVideo();};
  clearVideo=ButtonAt("×",1368,624,30,40,creation,delegate{Current.VideoPath="";EditCurrent();ShowCurrentMedia();RefreshRefs();});
  addVideo=ButtonAt("＋ 视频",1404,628,110,30,creation,delegate{ChooseVideo();});
  LabelAt("本地视频模型",992,676,150,18,9,creation);
  preset=ComboAt(992,698,876,creation);preset.Items.AddRange(new object[]{new Choice("hunyuan_t2v","HunyuanVideo 1.5 · 文生视频"),new Choice("hunyuan_i2v","HunyuanVideo 1.5 · 图生视频"),new Choice("ltx25_t2v","LTX-2.5 · 文生视频 · NVFP4"),new Choice("ltx25_i2v","LTX-2.5 · 图生视频 · NVFP4"),new Choice("h3_t2v","MiniMax H3 · 文生视频 · 音画同出"),new Choice("h3_i2v","MiniMax H3 · 图生视频 · 音画同出"),new Choice("ltx_t2v","LTX-2.3 · 文生视频"),new Choice("ltx_i2v","LTX-2.3 · 图生视频"),new Choice("musetalk_lipsync","MuseTalk · 对口型"),new Choice("cosyvoice_tts","CosyVoice · 配音"),new Choice("cosyvoice_lipsync","CosyVoice · 配音+对口型")});preset.SelectedIndex=0;preset.SelectedIndexChanged+=delegate{EditCurrent();UpdatePresetNote();RefreshRefs();};
  modelNote=LabelAt("检查模型文件后可交接出片",992,736,876,24,8.5f,creation);
  LabelAt("视频时长",992,764,100,18,9,creation);LabelAt("随机种子",1108,764,90,18,9,creation);
  fastLabel=LabelAt("LightX2V·Sage",1214,764,150,18,9,creation);
  duration=ComboAt(992,786,100,creation);foreach(int seconds in VideoDurations)duration.Items.Add(new Choice(seconds.ToString(),seconds+" 秒"));duration.SelectedIndex=2;duration.SelectedIndexChanged+=delegate{EditCurrent();};
  seed=TextAt(1108,786,90,32,creation);seed.Text="42";seed.AccessibleName="随机种子";seed.TextChanged+=delegate{EditCurrent();};
  fastSwitch=new ToggleSwitch();Add(fastSwitch,1214,786,56,28,9,creation);fastSwitch.CheckedChanged+=delegate{EditCurrent();};
  LabelAt("分辨率",1380,764,120,18,9,creation);
  size=ComboAt(1380,786,240,creation);size.Items.AddRange(new object[]{"横屏  768 × 448","横屏高清  1280 × 704","竖屏  448 × 768","竖屏高清  704 × 1280","方形  512 × 512","方形高清  1024 × 1024","全高清  1920 × 1088","竖屏全高清  1088 × 1920","2K  2560 × 1440","2K 竖屏  1440 × 2560","4K  3840 × 2176","4K 竖屏  2176 × 3840"});size.SelectedIndex=0;size.SelectedIndexChanged+=delegate{EditCurrent();};
  LabelAt("生成路径",1640,764,140,18,9,creation);computePath=ComboAt(1640,786,228,creation);computePath.Items.AddRange(new object[]{new Choice("fast","快速路径"),new Choice("hd","高清路径（省显存）")});computePath.SelectedIndex=1;computePath.AccessibleName="生成路径";computePath.AccessibleDescription="快速路径使用常规计算；高清路径使用省显存计算。H3 均为 Turbo 8 步，分辨率和时长沿用当前设置。";computePathTip.SetToolTip(computePath,computePath.AccessibleDescription);computePath.SelectedIndexChanged+=delegate{EditCurrent();};
  gpuState=LabelAt("●  连接中",1640,820,228,18,9,creation);gpuState.TextAlign=ContentAlignment.MiddleRight;
  gpuNote=LabelAt("点击「交接出片」后使用 GPU 进行本地视频生成。",992,818,560,22,8.5f,creation);
  cancelTask=ButtonAt("取消",1274,842,120,38,creation,async delegate{await Action("/cancel",new{},"正在取消当前任务…");});
  pauseTask=ButtonAt("暂停",1406,842,100,38,creation,async delegate{await Action("/pause",new{},"正在切换暂停状态…");});
  unload=ButtonAt("⏏",1518,842,38,38,creation,async delegate{await Action("/unload",new{},"正在卸载视频模型，等待显存释放…");});unload.AccessibleName="一键卸载视频模型：停止 ComfyUI 释放显存，不删除模型文件";
  run=ButtonAt("交接出片",1568,842,300,38,creation,async delegate{await PrepareAndHandoff();},true);
  progressTitle=LabelAt("生成进度 · 待提交",992,842,240,18,9,creation);
  generationMeter=new GenerationMeter();Add(generationMeter,992,866,240,8,9,creation);
  jobDetail=LabelAt("提交后显示实际生成进度",992,878,380,14,8f,creation);
  var foot=LabelAt("显卡交接  ·  工作台始终在线",1924,880,400,16,9,creation);foot.TextAlign=ContentAlignment.MiddleRight;
 }
 void BuildVoicePane(){
  LabelAt("配音 · 对口型",1888,116,300,26,13,creation);
  LabelAt("台词",1888,152,200,18,9,creation);
  ttsText=TextAt(1888,174,436,96,creation,true);ttsText.MaxLength=5000;ttsText.AccessibleName="配音台词";
  instructLabel=LabelAt("情绪指令（配音语气，可选）",1888,278,240,18,9,creation);
  instruct=TextAt(1888,300,436,40,creation);instruct.AccessibleName="情绪指令（配音语气，可选）";instruct.TextChanged+=delegate{EditCurrent();};
  LabelAt("音色",1888,350,150,18,9,creation);voiceChoice=ComboAt(1888,372,260,creation);
  LabelAt("语速",2164,350,120,18,9,creation);rateChoice=ComboAt(2164,372,160,creation);rateChoice.Items.AddRange(new object[]{new Choice("-10%","慢 -10%"),new Choice("+0%","标准 +0%"),new Choice("+10%","快 +10%"),new Choice("+20%","很快 +20%")});rateChoice.SelectedIndex=1;
  LabelAt("参考音频（克隆声线 / 对口型音源）",1888,414,280,18,9,creation);
  audioNote=LabelAt("未选择音频",1888,436,260,18,8.5f,creation);
  addAudio=ButtonAt("＋ 添加音频",1888,458,130,32,creation,delegate{ChooseAudio();});
  clearAudio=ButtonAt("×",2024,458,32,32,creation,delegate{Current.AudioPath="";EditCurrent();RefreshRefs();SaveDraft();});
  ttsRun=ButtonAt("生成配音",1888,502,180,40,creation,async delegate{await GenerateTts();},true);
  LabelAt("配音文件",1888,554,200,18,9,creation);
  ttsList=new ListBox{BorderStyle=BorderStyle.None,IntegralHeight=false,HorizontalScrollbar=true,DrawMode=DrawMode.OwnerDrawFixed};Add(ttsList,1888,576,436,110,9,creation);ttsList.DrawItem+=DrawVoiceItem;
  ttsPlay=ButtonAt("▶ 播放",1888,696,110,30,creation,delegate{var p=ttsList.SelectedItem as string;if(p!=null&&File.Exists(p))Process.Start(new ProcessStartInfo(p){UseShellExecute=true});});
  ttsOpen=ButtonAt("打开文件夹",2004,696,150,30,creation,delegate{OpenDirectory(Api.VoiceOutputRoot);});
  LabelAt("对口型",1888,738,300,20,11,creation);
  lipNote=LabelAt("对口型与声音克隆走 GPU 交接：在视频区的模型下拉里选 MuseTalk · 对口型 或 CosyVoice · 配音，配合上方参考音频交接出片。",1888,762,436,44,8.5f,creation);
  lipGoto=ButtonAt("视频区选中 MuseTalk 对口型",1888,814,220,32,creation,delegate{for(int i=0;i<preset.Items.Count;i++)if(((Choice)preset.Items[i]).Id=="musetalk_lipsync")preset.SelectedIndex=i;});
 }
 void BuildConnections(){
  LabelAt("连接你的创作助手",96,120,700,38,22,connections);
  LabelAt("用 Kimi 构思与优化提示词，让本机模型完成视频。",96,168,700,28,10,connections);
  LabelAt("Kimi API",96,236,600,30,15,connections);
  LabelAt("API Key",96,300,600,22,9,connections);key=TextAt(112,340,828,30,connections);key.UseSystemPasswordChar=true;key.AccessibleName="Kimi API Key";
  keyState=LabelAt("尚未读取密钥状态",96,392,860,25,9,connections);
  LabelAt("API 地址",96,452,860,22,9,connections);baseUrl=TextAt(112,492,828,29,connections);baseUrl.Text="https://api.moonshot.cn/v1";
  LabelAt("模型 ID",96,570,860,22,9,connections);apiModel=TextAt(112,610,828,29,connections);apiModel.Text="kimi-k2.6";
  saveKey=ButtonAt("加密保存",96,690,170,42,connections,async delegate{await SaveSettings();},true);
  testKey=ButtonAt("检查连接",282,690,170,42,connections,async delegate{await CheckConnection();});
  removeKey=ButtonAt("移除密钥",468,690,170,42,connections,async delegate{await RemoveKey();});
  LabelAt("密钥只为当前 Windows 用户加密保存。留空保存时保留已有密钥。",96,771,860,37,9,connections);
  LabelAt("Agent 接入",1028,120,600,30,15,connections);
  connectionDetail=LabelAt("等待检查 Codex、Qwen Agent 与 Kimi Code。",1028,186,844,365,10,connections);
  LabelAt("一个视频工作台，三个 Agent",1028,600,600,26,11,connections);
  LabelAt("在外部 Agent 中通过本地 MCP 工具准备与提交任务。这里的助手选择不会启动或嵌入它们的聊天界面。",1028,645,844,88,9,connections);
  ButtonAt("查看工具说明",1028,844,340,40,connections,delegate{OpenDocs();});
 }
 void BuildBound(){
  LabelAt("✓",96,150,80,60,34,bound).ForeColor=Dark?StudioPalette.Gold:Color.FromArgb(70,106,80);
  LabelAt("已绑定 Kimi API",170,158,600,42,20,bound);
  LabelAt("密钥已为本机当前 Windows 用户加密保存，只用于你发起的请求。",170,214,640,26,10,bound);
  boundCheckState=LabelAt("已保存 · 尚未检查连接",170,254,640,25,9,bound);
  LabelAt("模型 ID",96,320,200,22,9,bound);boundModel=LabelAt("",96,348,600,26,11,bound);
  LabelAt("API 地址",96,396,200,22,9,bound);boundUrl=LabelAt("",96,424,600,26,11,bound);
  boundBack=ButtonAt("返回创作",96,500,170,42,bound,delegate{ShowPage(0);},true);
  boundTest=ButtonAt("检查连接",282,500,170,42,bound,async delegate{await CheckConnection();});
  boundChange=ButtonAt("更换密钥",468,500,150,42,bound,delegate{EnterKeyEdit();});
  boundRemove=ButtonAt("移除密钥",634,500,150,42,bound,async delegate{await RemoveKey();});
  LabelAt("下一步",1028,120,600,30,15,bound);
  LabelAt("回到创作页描述画面，可添加参考图像或参考视频；交接出片后本机开始生成，完成后到「成片」页查看。",1028,186,844,150,10,bound);
  LabelAt("密钥变更说明",1028,600,600,26,11,bound);
  LabelAt("「更换密钥」返回表单重新输入；「移除密钥」立即删除本机保存的密钥。两者都不影响已生成的成片。",1028,645,844,88,9,bound);
  ButtonAt("查看工具说明",1028,844,340,40,bound,delegate{OpenDocs();});
  foreach(var c in bound)c.Visible=false;
 }
 internal bool IsBoundView {get{return Page==1&&showingBound;}}
 internal bool HasSavedKey {get{return keySaved;}}
 internal void ReportUiError(){if(closing||IsDisposed)return;SetNotice("界面操作未完成，错误已记录。若反复出现，可重新打开工作台；已提交的任务请先查看状态。",true);SetControls();}
 internal void EnterKeyEdit(){if(closing||IsDisposed)return;editingKey=true;ShowPage(1);key.Focus();}
 internal void ShowBoundCard(){if(IsDisposed||closing||!keySaved)return;editingKey=false;ShowPage(1);SetNotice("Kimi API 已绑定；可检查连接或返回创作。",false);}
 void RenderConnectionPage(){
  if(Page!=1||closing||IsDisposed)return;
  showingBound=keySaved&&!editingKey;
  foreach(var c in connections)c.Visible=!showingBound;
  foreach(var c in bound)c.Visible=showingBound;
  boundModel.Text=savedKimiModel;boundUrl.Text=savedKimiUrl;
  Invalidate(true);
 }
 internal void ApplyConnectionSettings(object settings,bool overwriteEditor){
  if(settings==null||closing||IsDisposed)return;
  bool saved=Json.Yes(settings,"key_saved");
  string model=Json.Str(settings,"kimi_model",savedKimiModel),url=Json.Str(settings,"kimi_base_url",savedKimiUrl);
  if(saved!=keySaved||model!=savedKimiModel||url!=savedKimiUrl)boundCheckState.Text=saved?"已保存 · 尚未检查连接":"尚未绑定";
  keySaved=saved;savedKimiModel=model;savedKimiUrl=url;latest["settings"]=settings;
  keyState.Text=keySaved?"已有加密密钥 · 输入新密钥可替换":"尚未保存密钥";
  if(overwriteEditor||!settingsLoaded){
   settingsLoaded=false;
   baseUrl.Text=Json.Str(settings,"kimi_base_url","https://api.moonshot.cn/v1");apiModel.Text=Json.Str(settings,"kimi_model","kimi-k2.6");
   string savedAgent=Json.Str(settings,"agent","kimi_api");
   for(int n=0;n<agent.Items.Count;n++)if(((Choice)agent.Items[n]).Id==savedAgent){agent.SelectedIndex=n;break;}
   settingsLoaded=true;
  }
  RenderConnectionPage();
 }
 void BuildLibrary(){
  LabelAt("成片",96,116,730,35,20,library);
  libraryNote=LabelAt("视频完成后，在这里预览和播放。",96,160,1060,26,10,library);
  libraryPreview=new MediaPreview();Add(libraryPreview,96,207,1060,640,10,library);libraryPreview.StatusChanged+=delegate(string text){SetNotice(text,false);};libraryPreview.Error+=delegate(string text){SetNotice(text,true);};
  LabelAt("本机成片库",1228,120,600,30,13,library);
  outputList=new ListBox{BorderStyle=BorderStyle.None,IntegralHeight=false,HorizontalScrollbar=true,DrawMode=DrawMode.OwnerDrawFixed};Add(outputList,1228,180,644,650,10,library);outputList.DrawItem+=DrawOutput;outputList.SelectedIndexChanged+=delegate{PreviewSelectedOutput();};
  ButtonAt("打开成片文件夹",1228,842,644,40,library,delegate{OpenDirectory(Api.OutputRoot);});
  LabelAt("选中右侧成片后，点击预览下方的播放按钮。",96,858,1060,22,9,library);
 }
 void ChooseImage(){if(Current.ImagePaths.Count>=6){SetNotice("参考图像最多 6 张。",true);return;}using(var d=new OpenFileDialog{Filter="图片|*.png;*.jpg;*.jpeg;*.webp",Multiselect=true,Title="选择参考图像（可多选，最多 6 张；用法随模型）"})if(d.ShowDialog(this)==DialogResult.OK){foreach(string f in d.FileNames){if(Current.ImagePaths.Count>=6)break;if(File.Exists(f)&&!Current.ImagePaths.Contains(f))Current.ImagePaths.Add(f);}if(Current.ImagePaths.Count>0)Current.VideoPath="";EditCurrent();RefreshRefs();if(Current.ImagePaths.Count>0)preview.ShowImage(Current.ImagePaths[Current.ImagePaths.Count-1]);else ShowCurrentMedia();SaveDraft();}}
 void RemoveImageAt(int slot){var paths=Current.ImagePaths;if(slot<0||slot>=paths.Count)return;paths.RemoveAt(slot);EditCurrent();RefreshRefs();ShowCurrentMedia();SaveDraft();}
 void ChooseVideo(){using(var d=new OpenFileDialog{Filter="视频|*.mp4;*.mov;*.webm;*.mkv",Title="选择参考视频"})if(d.ShowDialog(this)==DialogResult.OK){Current.VideoPath=d.FileName;if(File.Exists(d.FileName))Current.ImagePath="";EditCurrent();RefreshRefs();preview.ShowVideo(d.FileName);SaveDraft();}}
 void ChooseAudio(){using(var d=new OpenFileDialog{Filter="音频|*.wav;*.mp3;*.m4a;*.flac;*.ogg;*.aac",Title="选择参考音频（配音音色 3-10 秒 / 对口型音源）"})if(d.ShowDialog(this)==DialogResult.OK){Current.AudioPath=d.FileName;EditCurrent();RefreshRefs();SaveDraft();}}
 void RefreshRefs(){if(populating)return;populating=true;try{var paths=Current.ImagePaths;for(int i=0;i<6;i++){bool has=i<paths.Count;imageThumbs[i].Visible=has;if(has)imageThumbs[i].SetMedia(paths[i],false);imageRemoves[i].Visible=has;}int slot=Math.Min(paths.Count,5);var rect=new Rectangle(992+slot*46,624,40,40);positions[addImage]=rect;float sc=UiScale;addImage.SetBounds((int)(rect.X*sc),(int)(rect.Y*sc),(int)(rect.Width*sc),(int)(rect.Height*sc));addImage.Visible=paths.Count<6;videoThumb.SetMedia(Current.VideoPath,true);clearVideo.Enabled=File.Exists(Current.VideoPath);if(audioNote!=null)audioNote.Text=string.IsNullOrEmpty(Current.AudioPath)?"未选择音频":Path.GetFileName(Current.AudioPath);if(clearAudio!=null)clearAudio.Enabled=File.Exists(Current.AudioPath);}finally{populating=false;}}
 void LoadShot(){bool matched=false;populating=true;try{var s=Current;matched=s.NormalizeReferenceMode();if(matched){s.Invalidate();dirty=true;}prompt.Text=s.Prompt;if(content!=null)content.Text=s.Content??"";if(instruct!=null)instruct.Text=s.Instruct??"";seed.Text=s.Seed.ToString();fastSwitch.Set(s.Fast);if(computePath!=null)computePath.SelectedIndex=s.ComputePath=="fast"?0:1;int di=-1;for(int n=0;n<duration.Items.Count;n++)if(((Choice)duration.Items[n]).Id==s.Seconds.ToString()){di=n;break;}if(di<0){duration.Items.Add(new Choice(s.Seconds.ToString(),s.Seconds+" 秒"));di=duration.Items.Count-1;}duration.SelectedIndex=di;for(int i=0;i<preset.Items.Count;i++)if(((Choice)preset.Items[i]).Id==s.Preset){preset.SelectedIndex=i;break;}int si=0;for(int i=0;i<SizeW.Length;i++)if(SizeW[i]==s.Width&&SizeH[i]==s.Height){si=i;break;}size.SelectedIndex=si;}finally{populating=false;}RebuildShotChoices();RefreshRefs();ShowCurrentMedia();UpdatePresetNote();SetControls();if(matched){SetNotice("已保留参考素材，自动切换为同模型的图生视频。",false);SaveDraft();}}
 void ShowCurrentMedia(){if(Testing)return;string output=Current.Outputs.Count>0?Current.Outputs[Current.Outputs.Count-1]:"";if(IsOutput(output))preview.ShowVideo(output);else if(File.Exists(Current.VideoPath))preview.ShowVideo(Current.VideoPath);else if(File.Exists(Current.ImagePath))preview.ShowImage(Current.ImagePath);else preview.Clear();}
 bool MatchReferenceMode(Shot s){if(!s.NormalizeReferenceMode())return false;bool wasPopulating=populating;populating=true;try{for(int i=0;i<preset.Items.Count;i++)if(((Choice)preset.Items[i]).Id==s.Preset){preset.SelectedIndex=i;break;}}finally{populating=wasPopulating;}return true;}
 void EditCurrent(){if(populating||prompt==null||preset==null||size==null||seed==null||duration==null)return;var s=Current;s.Prompt=prompt.Text;s.Content=content!=null?content.Text:"";s.Preset=PresetId;bool matched=MatchReferenceMode(s);s.Instruct=instruct!=null?instruct.Text:"";s.Fast=fastSwitch.Checked;s.ComputePath=ComputePathId;s.Width=SizeW[Math.Min(size.SelectedIndex,SizeW.Length-1)];s.Height=SizeH[Math.Min(size.SelectedIndex,SizeH.Length-1)];int seconds;var selectedDuration=duration.SelectedItem as Choice;if(selectedDuration!=null&&int.TryParse(selectedDuration.Id,out seconds))s.Seconds=seconds;long value;if(long.TryParse(seed.Text,out value)&&value>=0)s.Seed=value;s.Invalidate();dirty=true;if(!Testing){saveDraft.Stop();saveDraft.Start();}UpdatePresetNote();SetControls();if(matched)SetNotice("已保留参考素材，自动切换为同模型的图生视频。",false);}
 void SaveDraft(){if(Testing||!dirty)return;try{DraftStore.Save(shots);dirty=false;}catch(Exception e){if(!closing)SetNotice("草稿尚未保存："+SafeError(e),true);}}
 string AgentId{get{return agent.SelectedItem==null?"kimi_api":((Choice)agent.SelectedItem).Id;}}
 string PresetId{get{return preset.SelectedItem==null?"hunyuan_t2v":((Choice)preset.SelectedItem).Id;}}
 string ComputePathId{get{return computePath==null||computePath.SelectedItem==null?"hd":((Choice)computePath.SelectedItem).Id;}}
 void UpdateAgentNote(){if(agentNote==null)return;agentNote.Text=AgentId=="kimi_api"?"Kimi 协助构思，视频由本地模型生成。":"任务经所选 Agent 的 MCP 工具接入；优化提示词与内容也走所选 Agent。";bool polishOk=AgentId!="kimi_api"||keySaved;if(improve!=null)improve.Enabled=!working&&!Json.Yes(controller,"busy")&&connected&&polishOk;if(improveContent!=null)improveContent.Enabled=improve.Enabled;}
 async Task PersistAgent(){if(working)return;string value=AgentId;working=true;SetControls();try{await Task.Run(()=>Api.Request("/settings",new{agent=value}));}catch{SetNotice("助手选择暂未保存，连接恢复后可重试。",true);}finally{working=false;SetControls();}}
 void UpdatePresetNote(){if(modelNote==null)return;string text=connected?"模型状态待检查":"等待本地服务连接";foreach(object item in Json.Items(Json.Get(latest,"catalog"))){if(Json.Str(item,"id")==PresetId){text=Json.Str(item,"detail",Json.Yes(item,"ready")?"模型文件已就绪":"模型文件尚未就绪");break;}}if(Current.Seconds>10)text+=" · 长时长生成更慢，显存占用可能增加";if(Current.ImagePaths.Count>1){if(PresetId.StartsWith("h3",StringComparison.Ordinal))text+=" · 多图参考融合，提示词用 <Picture 1>… 指代";else if(PresetId.StartsWith("hunyuan",StringComparison.Ordinal))text+=" · 多图作为视觉参考理解（非首帧）";else if(PresetId.StartsWith("ltx25",StringComparison.Ordinal))text+=" · 多图作为关键帧均匀锚定";else text+=" · 当前模型仅使用第 1 张为首帧";}modelNote.Text=text;if(fastLabel!=null)fastLabel.Text=EngineName();}
 string EngineName(){string p=PresetId;if(computePath!=null&&computePath.Items.Count==2){bool eight=p.StartsWith("h3")||p.StartsWith("ltx");computePath.Items[0]=new Choice("fast",eight?"快速路径（8步）":"快速路径");computePath.Invalidate();computePathTip.SetToolTip(computePath,eight?"快速路径：常规 8 步生成。高清路径：使用省显存计算，仍保持 8 步。两者均沿用所选分辨率、时长和参考图。":"快速路径：使用常规计算。高清路径：使用省显存计算。采样配置由加速开关决定，两者均沿用所选分辨率、时长和参考图。");}if(p.StartsWith("h3"))return "Turbo8步·原生音频";if(p.StartsWith("musetalk"))return "MuseTalk";if(p.StartsWith("cosyvoice"))return "CosyVoice3";if(p.StartsWith("ltx"))return "SageAttention";if(p=="hunyuan_i2v")return "CFG蒸馏·Sage";return "LightX2V·Sage";}
 async Task ImproveBox(TextBox target,string which){if(target==null||target.Text.Trim().Length==0){SetNotice("请先在"+which+"栏填写想生成的画面。",true);return;}string polishAgent=AgentId;if(polishAgent=="kimi_api"&&!keySaved){SetNotice("优化提示词使用 Kimi API 前需要先在连接页绑定 Kimi Key。",true);return;}working=true;SetControls();SetNotice("正在经所选 Agent 优化"+which+"…",false);try{var body=new{agent=polishAgent,prompt=target.Text};var result=await Task.Run(()=>Api.Request("/improve",body,180000));if(closing||IsDisposed)return;string improved=Json.Str(result,"prompt");if(improved.Length==0)throw new InvalidOperationException("助手没有返回提示词。");target.Text=improved;SetNotice(which+"已更新，可继续修改。",false);}catch(Exception e){SetNotice(SafeError(e),true);}finally{working=false;SetControls();}}
 bool IsToolPreset{get{string p=PresetId;return p=="musetalk_lipsync"||p=="cosyvoice_tts"||p=="cosyvoice_lipsync";}}
 async Task PrepareImage(){if(imgPrompt.Text.Trim().Length==0){SetNotice("先填写出图描述。",true);return;}long sd;if(!long.TryParse(imgSeed.Text,out sd)||sd<0){SetNotice("出图种子需要非负整数。",true);return;}working=true;SetControls();try{int w=1328,h=1328;if(imgRatio.SelectedIndex==1){w=1664;h=928;}else if(imgRatio.SelectedIndex==2){w=928;h=1664;}SetNotice("正在准备出图工作流…",false);var result=await Task.Run(()=>Api.Request("/prepare-image",new{prompt=imgPrompt.Text,width=w,height=h,seed=sd,lightning=lightningSwitch.Checked},45000));if(closing||IsDisposed)return;string wp=Json.Str(result,"workflow_path");if(!Json.Yes(result,"ready")||wp.Length==0)throw new InvalidOperationException(Json.Str(result,"detail","图像模型尚未就绪"));SetNotice("正在交接出图…",false);var handoff=await Task.Run(()=>Api.Request("/handoff",new{workflow_paths=new[]{wp}},30000));if(closing||IsDisposed)return;SetNotice(Json.Str(handoff,"detail","已提交出图任务。"),false);await RefreshStatus();}catch(Exception e){SetNotice(SafeError(e),true);}finally{working=false;SetControls();}}
 async Task GenerateTts(){if(ttsText.Text.Trim().Length==0){SetNotice("先填写配音台词。",true);return;}working=true;SetControls();SetNotice("正在生成配音…",false);try{string voice=voiceChoice.SelectedItem==null?"zh-CN-XiaoxiaoNeural":((Choice)voiceChoice.SelectedItem).Id;string rate=rateChoice.SelectedItem==null?"+0%":((Choice)rateChoice.SelectedItem).Id;var result=await Task.Run(()=>Api.Request("/tts",new{text=ttsText.Text,voice=voice,rate=rate},180000));if(closing||IsDisposed)return;SetNotice("配音完成："+Json.Str(result,"name"),false);RefreshVoiceFiles();}catch(Exception e){SetNotice(SafeError(e),true);}finally{working=false;SetControls();}}
 void RefreshVoiceFiles(){if(Testing||ttsList==null)return;ttsList.BeginUpdate();ttsList.Items.Clear();try{string dir=Api.VoiceOutputRoot;if(Directory.Exists(dir)){var files=Directory.GetFiles(dir,"*.mp3");Array.Sort(files,(a,b)=>File.GetLastWriteTime(b).CompareTo(File.GetLastWriteTime(a)));foreach(var f in files)ttsList.Items.Add(f);}}catch{}ttsList.EndUpdate();}
 async Task RefreshImageFiles(){if(Testing||imgList==null)return;try{var result=await Task.Run(()=>Api.Request("/image-outputs"));if(closing||IsDisposed)return;var paths=new List<string>();foreach(object o in Json.Items(Json.Get(result,"outputs"))){string p=Json.Str(o,"path");if(File.Exists(p))paths.Add(p);}imgList.BeginUpdate();imgList.Items.Clear();foreach(var p in paths)imgList.Items.Add(p);imgList.EndUpdate();}catch{}}
 bool voicesLoaded;
 async Task LoadVoices(){if(voicesLoaded||Testing||voiceChoice==null)return;try{var result=await Task.Run(()=>Api.Request("/voices",null));if(closing||IsDisposed)return;var items=Json.Items(Json.Get(result,"voices"));voiceChoice.Items.Clear();foreach(object v in items)voiceChoice.Items.Add(new Choice(Json.Str(v,"id"),Json.Str(v,"name")));if(voiceChoice.Items.Count>0)voiceChoice.SelectedIndex=0;voicesLoaded=true;}catch{}}
 void DrawImageItem(object sender,DrawItemEventArgs e){if(e.Index<0)return;bool sel=(e.State&DrawItemState.Selected)!=0;using(var b=new SolidBrush(sel?(Dark?StudioPalette.Selection:Color.FromArgb(234,240,234)):Card))e.Graphics.FillRectangle(b,e.Bounds);string path=Convert.ToString(imgList.Items[e.Index]);float s=UiScale;TextRenderer.DrawText(e.Graphics,Path.GetFileName(path),imgList.Font,new Rectangle(e.Bounds.X+(int)(10*s),e.Bounds.Y+(int)(4*s),e.Bounds.Width-(int)(20*s),(int)(24*s)),Ink,TextFormatFlags.EndEllipsis);}
 void DrawVoiceItem(object sender,DrawItemEventArgs e){if(e.Index<0)return;bool sel=(e.State&DrawItemState.Selected)!=0;using(var b=new SolidBrush(sel?(Dark?StudioPalette.Selection:Color.FromArgb(234,240,234)):Card))e.Graphics.FillRectangle(b,e.Bounds);string path=Convert.ToString(ttsList.Items[e.Index]);float s=UiScale;TextRenderer.DrawText(e.Graphics,Path.GetFileName(path),ttsList.Font,new Rectangle(e.Bounds.X+(int)(10*s),e.Bounds.Y+(int)(4*s),e.Bounds.Width-(int)(20*s),(int)(24*s)),Ink,TextFormatFlags.EndEllipsis);}
 bool ValidateShot(Shot shot){
  if(IsToolPreset){
   long seedValue;if(!long.TryParse(seed.Text,out seedValue)||seedValue<0){SetNotice("随机种子需要填写非负整数。",true);return false;}shot.Seed=seedValue;
   if(shot.Preset=="musetalk_lipsync"){
    if(!File.Exists(shot.VideoPath)){SetNotice("对口型需要选择参考视频。",true);return false;}
    if(!File.Exists(shot.AudioPath)){SetNotice("对口型需要选择配音音频。",true);return false;}
   }else{
    if(shot.Prompt.Trim().Length==0){SetNotice("配音需要输入台词文本。",true);return false;}
    if(!File.Exists(shot.AudioPath)){SetNotice("配音需要选择 3–10 秒的参考音色音频。",true);return false;}
    if(shot.Preset=="cosyvoice_lipsync"&&!File.Exists(shot.VideoPath)){SetNotice("配音+对口型需要选择参考视频。",true);return false;}
   }
   try{shot.Validate();return true;}catch(Exception e){SetNotice(SafeError(e),true);return false;}
  }
  if((shot.Prompt+shot.Content).Trim().Length==0){SetNotice("请先设置提示词或填写视频内容。",true);return false;}if(FullPrompt(shot).Length>16000){SetNotice("提示词与内容合计最多 16000 字。",true);return false;}long value;if(!long.TryParse(seed.Text,out value)||value<0){SetNotice("随机种子需要填写非负整数。",true);return false;}shot.Seed=value;if(File.Exists(shot.ImagePath)&&File.Exists(shot.VideoPath)){SetNotice("参考图像与参考视频请二选一。",true);return false;}if(shot.Preset.EndsWith("i2v")&&!File.Exists(shot.ImagePath)&&!File.Exists(shot.VideoPath)){SetNotice("图生视频需要有效的参考图像或参考视频。",true);return false;}try{shot.Validate();return true;}catch(Exception e){SetNotice(SafeError(e),true);return false;}}
 static string FullPrompt(Shot s){string p=(s.Prompt??"").Trim(),c=(s.Content??"").Trim();if(p.Length==0)return c;if(c.Length==0)return p;return p+"\n"+c;}
 async Task PrepareAndHandoff(){var s=Current;if(!Json.Yes(controller,"supports_compute_paths")){SetNotice("生成路径功能等待控制服务重新载入，请先完成当前任务。",true);return;}if(MatchReferenceMode(s)){s.Invalidate();dirty=true;SaveDraft();}if(!ValidateShot(s))return;working=true;submittingVideo=true;SetControls();try{SetNotice("正在准备工作流…",false);var body=new{title=s.Title,preset=s.Preset,prompt=FullPrompt(s),image_path=s.ImagePath,image_paths=s.ImagePaths.ToArray(),video_path=s.VideoPath,audio_path=s.AudioPath,instruct_text=s.Instruct,width=s.Width,height=s.Height,seconds=s.Seconds,seed=s.Seed,fast=s.Fast,compute_path=s.ComputePath};var result=await Task.Run(()=>Api.Request("/prepare",body,45000));if(closing||IsDisposed)return;s.WorkflowPath=Json.Str(result,"workflow_path");bool ready=Json.Yes(result,"ready")&&s.WorkflowPath.Length>0;s.Phase=ready?"prepared":"error";dirty=true;SaveDraft();if(!ready)throw new InvalidOperationException(Json.Str(result,"detail","模型或工作流尚未就绪。"));SetNotice("正在交接…",false);var handoff=await Task.Run(()=>Api.Request("/handoff",new{workflow_paths=new[]{s.WorkflowPath}},30000));if(closing||IsDisposed)return;s.Phase="queued";dirty=true;SaveDraft();SetNotice(Json.Str(handoff,"detail","已提交视频队列。"),false);await RefreshStatus();}catch(Exception e){SaveDraft();SetNotice(SafeError(e),true);}finally{working=false;submittingVideo=false;SetControls();}}
 static object SavedSettingsResponse(Dictionary<string,object> response){
  object settings=Json.Get(response,"settings");
  if(!Json.Yes(response,"ok")||!(Json.Get(settings,"key_saved") is bool))throw new InvalidOperationException("保存结果未确认，请保留输入并重试。");
  return settings;
 }
 internal async Task SaveSettings(){
  if(working||closing||IsDisposed)return;
  working=true;settingsRevision++;
  try{
   SetControls();bool hadKey=key.Text.Trim().Length>0;
   var body=new Dictionary<string,object>{{"agent",AgentId},{"kimi_base_url",baseUrl.Text.Trim()},{"kimi_model",apiModel.Text.Trim()}};
   if(hadKey)body["kimi_api_key"]=key.Text.Trim();
   var result=await Task.Run(()=>connectionRequest("/settings",body,20000));
   if(closing||IsDisposed)return;
   object settings=SavedSettingsResponse(result);
   if(hadKey&&!Json.Yes(settings,"key_saved"))throw new InvalidOperationException("密钥保存未确认，输入已保留，请重试。");
   ApplyConnectionSettings(settings,true);key.Clear();
   if(keySaved)ShowBoundCard();
   else{editingKey=true;ShowPage(1);SetNotice("设置已保存，填入 API Key 后即可绑定。",false);}
  }catch(Exception e){if(!closing&&!IsDisposed)SetNotice(SafeError(e),true);}
  finally{working=false;if(!closing&&!IsDisposed)SetControls();}
 }
 internal async Task RemoveKey(){
  if(working||closing||IsDisposed)return;
  working=true;settingsRevision++;
  try{
   SetControls();var result=await Task.Run(()=>connectionRequest("/settings",new{remove_key=true},20000));
   if(closing||IsDisposed)return;
   object settings=SavedSettingsResponse(result);
   if(Json.Yes(settings,"key_saved"))throw new InvalidOperationException("移除结果未确认，原绑定状态已保留。");
   ApplyConnectionSettings(settings,true);key.Clear();editingKey=true;ShowPage(1);
   SetNotice("已移除本软件保存的 Kimi 密钥。",false);
  }catch(Exception e){if(!closing&&!IsDisposed)SetNotice(SafeError(e),true);}
  finally{working=false;if(!closing&&!IsDisposed)SetControls();}
 }
 internal async Task CheckConnection(){
  if(working||closing||IsDisposed)return;
  if(!keySaved){SetNotice("请先保存 Kimi API Key。",true);return;}
  working=true;int revision=settingsRevision;
  try{
   SetControls();boundCheckState.Text="正在检查连接…";SetNotice("正在检查已保存的 Kimi 连接…",false);
   var result=await Task.Run(()=>connectionRequest("/test-connection",new{agent="kimi_api"},30000));
   if(closing||IsDisposed||revision!=settingsRevision)return;
   if(!Json.Yes(result,"ok"))throw new InvalidOperationException("连接检查未通过，请重试。");
   string detail=Json.Str(result,"detail","连接检查已完成。");
   boundCheckState.Text=detail;SetNotice(detail,!Json.Yes(result,"model_available"));
  }catch(Exception e){if(!closing&&!IsDisposed){boundCheckState.Text="检查未通过 · 密钥仍已保存";SetNotice(SafeError(e),true);}}
  finally{working=false;if(!closing&&!IsDisposed)SetControls();}
 }
 async Task Action(string path,object body,string text){working=true;SetControls();SetNotice(text,false);try{var result=await Task.Run(()=>Api.Request(path,body,20000));SetNotice(Json.Str(result,"detail",Json.Str(result,"message","操作已提交。")),false);await RefreshStatus();}catch(Exception e){SetNotice(SafeError(e),true);}finally{working=false;SetControls();}}
 string SafeError(Exception e){return e is WebException?"本地服务尚未连接，请稍候；若持续离线，请重新打开工作台。":e.Message.Length>300?e.Message.Substring(0,300):e.Message;}
 void SetNotice(string text,bool error){if(closing)return;noticeText=text;lastError=error?text:"";if(notice!=null){notice.Text=text;notice.ForeColor=error?(Dark?StudioPalette.Error:Color.FromArgb(165,66,47)):Muted;}}
 async Task RefreshStatus(){if(polling||Testing||closing)return;polling=true;int revision=settingsRevision;try{var result=await Task.Run(()=>Api.Request("/status"));if(closing||IsDisposed)return;if(Json.Str(result,"service")!="video-studio"||Convert.ToInt32(Json.Get(result,"api_version")??0)!=1)throw new InvalidOperationException("端口上的服务与视频工作台不匹配。");connected=true;ApplyStatus(result,revision==settingsRevision&&!working);}catch(WebException e){if(closing||IsDisposed)return;connected=false;globalState.Text="本地服务连接中";gpuState.Text="●  离线";if(e.Status==WebExceptionStatus.ConnectFailure){try{Api.EnsureService();}catch{}}SetControls();}catch(Exception e){if(closing||IsDisposed)return;connected=false;globalState.Text="服务需要检查";SetNotice(SafeError(e),true);SetControls();}finally{polling=false;}}
 internal void ApplyStatus(Dictionary<string,object> data,bool applySettings=true){if(closing||IsDisposed)return;connected=true;object previousSettings=Json.Get(latest,"settings");latest=new Dictionary<string,object>(data);if(!applySettings&&previousSettings!=null)latest["settings"]=previousSettings;controller=Json.Get(data,"controller");bool busy=Json.Yes(controller,"busy");poll.Interval=busy?1000:3500;globalState.Text=busy?"●  正在运行视频任务":"视频工作台在线";gpuState.Text=busy?"●  生成中":Json.Yes(controller,"comfy_running")?"●  视频引擎在线":"●  引擎待命";jobDetail.Text=Json.Str(controller,"detail",busy?"视频任务处理中":"等待交接出片");if(String.IsNullOrWhiteSpace(jobDetail.Text))jobDetail.Text="点击交接出片开始生成。";
  if(applySettings)ApplyConnectionSettings(Json.Get(data,"settings"),false);
  StringBuilder list=new StringBuilder();foreach(object a in Json.Items(Json.Get(data,"agents"))){list.Append(Json.Str(a,"name",Json.Str(a,"id"))).Append(Json.Yes(a,"configured")||Json.Yes(a,"registered")?"  ·  已配置":"  ·  待接入").AppendLine().Append(Json.Str(a,"detail")).AppendLine().AppendLine();}if(list.Length>0)connectionDetail.Text=list.ToString();
  var tasks=Json.Get(data,"tasks")??Json.Get(controller,"tasks");foreach(object task in Json.Items(tasks)){string path=Json.Str(task,"workflow_path");string phase=Json.Str(task,"phase","prepared");if(String.Equals(Current.WorkflowPath,path,StringComparison.OrdinalIgnoreCase)){string normalized=NormalizePhase(phase);var outs=new List<string>();foreach(object o in Json.Items(Json.Get(task,"outputs"))){string p=o is string?(string)o:Json.Str(o,"path");if(IsOutput(p))outs.Add(p);}if(Current.Phase!=normalized||String.Join("|",Current.Outputs)!=String.Join("|",outs)){Current.Phase=normalized;Current.Outputs=outs;dirty=true;if(outs.Count>0)ShowCurrentMedia();}}}
  object selected=outputList.SelectedItem;outputList.BeginUpdate();outputList.Items.Clear();foreach(object o in Json.Items(Json.Get(data,"outputs")??Json.Get(controller,"outputs"))){string path=o is string?(string)o:Json.Str(o,"path");if(IsOutput(path))outputList.Items.Add(path);}if(selected!=null&&outputList.Items.Contains(selected))outputList.SelectedItem=selected;outputList.EndUpdate();libraryNote.Text=outputList.Items.Count>0?outputList.Items.Count+" 个 MP4 · 选择成片预览":"还没有成片。视频生成完成后，会显示在这里。";
  if(imgList!=null){object selImg=imgList.SelectedItem;imgList.BeginUpdate();imgList.Items.Clear();foreach(object o in Json.Items(Json.Get(data,"image_outputs"))){string path=o is string?(string)o:Json.Str(o,"path");if(File.Exists(path))imgList.Items.Add(path);}if(selImg!=null&&imgList.Items.Contains(selImg))imgList.SelectedItem=selImg;imgList.EndUpdate();if(!voicesLoaded){var ignored=LoadVoices();}}UpdatePresetNote();SaveDraft();SetControls();Invalidate();
 }
 static string NormalizePhase(string phase){if(phase=="done"||phase=="completed")return "done";if(phase=="failed"||phase=="skipped")return phase;if(phase=="cancelled")return "cancelled";if(phase=="error")return "error";if(phase=="running"||phase=="generating")return "running";if(phase=="queued")return "queued";if(phase=="prepared")return "prepared";return "draft";}
 internal static string PhaseText(string phase){switch(NormalizePhase(phase)){case "prepared":return "已准备";case "queued":return "排队中";case "running":return "生成中";case "done":return "已完成";case "failed":return "失败";case "skipped":return "已跳过";case "cancelled":return "已取消";case "error":return "需检查";default:return "草稿";}}
 internal static bool CanPrepareAndHandoff(object control,bool serviceConnected,bool submitting){
  if(submitting||!serviceConnected||!Json.Yes(control,"connected")||!(Json.Get(control,"busy") is bool)||!(Json.Get(control,"comfy_running") is bool)||Json.Yes(control,"busy"))return false;
  bool comfy=Json.Yes(control,"comfy_running");
  if(Json.Obj(control).ContainsKey("can_accept_batch")){
   if(!Json.Yes(control,"can_accept_batch"))return false;
   return !comfy||(Json.Yes(control,"supports_warm_handoff")&&Json.Yes(control,"warm_handoff_ready")&&Json.Yes(control,"comfy_idle"));
  }
  // Older controllers support a cold handoff only. A draft need not have a prepared graph yet.
  return !comfy;
 }
 void SetControls(){
  if(closing||IsDisposed||run==null)return;
  bool busy=Json.Yes(controller,"busy");foreach(Control c in creation)c.Enabled=!working&&!busy;
  preview.Enabled=true;run.Enabled=CanPrepareAndHandoff(controller,connected,working);
  var progressView=GenerationProgressState.From(controller,connected,submittingVideo);progressTitle.Text=progressView.Title;jobDetail.Text=progressView.Detail;generationMeter.SetProgress(progressView);generationMeter.Enabled=progressTitle.Enabled=jobDetail.Enabled=true;
  bool canUnload=Json.Obj(controller).ContainsKey("can_unload")?Json.Yes(controller,"can_unload"):Json.Yes(controller,"comfy_running");
  unload.Enabled=!working&&!busy&&connected&&canUnload;
  pauseTask.Enabled=cancelTask.Enabled=!working&&connected&&busy;pauseTask.Text=Json.Yes(controller,"paused")?"继续":"暂停";
  if(imgRun!=null)imgRun.Enabled=!working&&!busy&&connected;
  if(ttsRun!=null)ttsRun.Enabled=!working&&!busy&&connected;
  saveKey.Enabled=removeKey.Enabled=key.Enabled=baseUrl.Enabled=apiModel.Enabled=!working&&connected;
  testKey.Enabled=!working&&connected&&keySaved;navCreate.Enabled=navConnect.Enabled=navLibrary.Enabled=!working;
  clearVideo.Enabled=File.Exists(Current.VideoPath);
  if(boundTest!=null){boundTest.Enabled=boundRemove.Enabled=!working&&connected&&keySaved;boundChange.Enabled=!working;}
  if(!connected||!Json.Yes(controller,"connected"))gpuNote.Text="正在连接视频控制服务，连接恢复后可交接出片。";
  else if(busy)gpuNote.Text="当前任务仍在处理，完成后可继续提交下一条。";
  else if(Json.Yes(controller,"comfy_running"))gpuNote.Text=run.Enabled?"视频引擎已就绪，可直接交接下一条任务。":Json.Str(controller,"accept_detail","视频引擎暂时不能接收新任务，请稍候重试。");
  else gpuNote.Text="点击「交接出片」后使用 GPU 进行本地视频生成。";
  run.AccessibleDescription=gpuNote.Text;
  ApplyStatusColors();UpdateAgentNote();
 }
 internal void ShowPage(int page){if(closing||IsDisposed)return;Page=page;showingBound=false;foreach(var c in bound)c.Visible=false;foreach(var c in creation)c.Visible=page==0;foreach(var c in connections)c.Visible=page==1;foreach(var c in library)c.Visible=page==2;navCreate.Selected=page==0;navConnect.Selected=page==1;navLibrary.Selected=page==2;if(page==0)RefreshRefs();if(page==1)RenderConnectionPage();Invalidate(true);}
 void ApplyStatusColors(){
  if(gpuState==null||progressTitle==null)return;
  bool online=connected&&Json.Yes(controller,"connected"),busy=online&&Json.Yes(controller,"busy");
  gpuState.ForeColor=!online?Muted:busy?Accent:Json.Yes(controller,"comfy_running")?(Dark?StudioPalette.Success:Color.FromArgb(70,106,80)):Muted;
  globalState.ForeColor=busy?Accent:Muted;
  var view=GenerationProgressState.From(controller,connected,submittingVideo);
  progressTitle.ForeColor=view.Failed?(Dark?StudioPalette.Error:Color.FromArgb(165,66,47)):view.Indeterminate||view.Percent>0?Accent:Muted;
  if(boundModel!=null)boundModel.ForeColor=Ink;if(boundUrl!=null)boundUrl.ForeColor=Ink;
 }
 Font UiFont(float size,FontStyle style=FontStyle.Regular){string k=size+"/"+style;Font f;if(!fonts.TryGetValue(k,out f)){f=new Font("Microsoft YaHei UI",size,style,GraphicsUnit.Pixel);fonts[k]=f;}return f;}
 float FitScale(uint dpi){float s=dpi>0?dpi/96f:1f;try{var wa=Screen.FromHandle(Handle).WorkingArea;s=Math.Min(s,Math.Min(wa.Width/2440f,wa.Height/964f));}catch{}return s;}
 internal void LayoutScale(float scale){UiScale=scale;SuspendLayout();ClientSize=new Size((int)(2400*scale),(int)(900*scale));foreach(var pair in positions){var r=pair.Value;pair.Key.SetBounds((int)(r.X*scale),(int)(r.Y*scale),(int)(r.Width*scale),(int)(r.Height*scale));pair.Key.Font=UiFont(fontSizes[pair.Key]*1.3333f*scale,pair.Key is Label&&fontSizes[pair.Key]>=13?FontStyle.Bold:FontStyle.Regular);var b=pair.Key as QuietButton;if(b!=null)b.ScaleValue=scale;var t=pair.Key as RefThumb;if(t!=null)t.ScaleValue=scale;var sw=pair.Key as ToggleSwitch;if(sw!=null)sw.ScaleValue=scale;}preview.UiScale=libraryPreview.UiScale=imgPreview.UiScale=scale;outputList.ItemHeight=(int)(64*scale);imgList.ItemHeight=(int)(28*scale);ttsList.ItemHeight=(int)(28*scale);ResumeLayout();Invalidate(true);}
 [DllImport("uxtheme.dll",CharSet=CharSet.Unicode)] static extern int SetWindowTheme(IntPtr hwnd,string appName,string idList);
 void ApplyNativeTheme(Control c){if(!c.IsHandleCreated)return;try{SetWindowTheme(c.Handle,Dark?"DarkMode_Explorer":"Explorer",null);}catch{}}
 [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd,int attribute,ref int value,int size);
 internal void SetTheme(bool dark,bool persist){Dark=dark;BackColor=Bg;foreach(Control c in Controls){c.ForeColor=c is Label&&fontSizes[c]<13?Muted:Ink;c.BackColor=c is Label?Color.Transparent:c is TextBox||c is ChoiceBox||c is ListBox?Card:Bg;if(c is TextBox)ApplyNativeTheme(c);var b=c as QuietButton;if(b!=null){b.Dark=Dark;b.Surface=connections.Contains(c)||bound.Contains(c)||library.Contains(c)?Card:Bg;}var choice=c as ChoiceBox;if(choice!=null)choice.Dark=Dark;var t=c as RefThumb;if(t!=null)t.Dark=Dark;var sw=c as ToggleSwitch;if(sw!=null)sw.Dark=Dark;var meter=c as GenerationMeter;if(meter!=null)meter.Dark=Dark;}preview.Dark=libraryPreview.Dark=imgPreview.Dark=Dark;theme.Text=Dark?"☼":"◐";if(IsHandleCreated)try{int v=Dark?1:0;DwmSetWindowAttribute(Handle,20,ref v,4);}catch{}if(persist&&!Testing)try{File.WriteAllText(themeFile,Json.Serializer.Serialize(new{dark=Dark}));}catch{}if(bound.Count>0)bound[0].ForeColor=Dark?StudioPalette.Success:Color.FromArgb(70,106,80);ApplyStatusColors();if(notice!=null)SetNotice(noticeText,lastError.Length>0);Invalidate(true);}
 internal static GraphicsPath Rounded(RectangleF r,float radius){float d=Math.Min(radius*2,Math.Min(r.Width,r.Height));var p=new GraphicsPath();p.AddArc(r.X,r.Y,d,d,180,90);p.AddArc(r.Right-d,r.Y,d,d,270,90);p.AddArc(r.Right-d,r.Bottom-d,d,d,0,90);p.AddArc(r.X,r.Bottom-d,d,d,90,90);p.CloseFigure();return p;}
 void Box(Graphics g,int x,int y,int w,int h,int radius=12){using(var p=Rounded(new RectangleF(x,y,w,h),radius))using(var b=new SolidBrush(Card))g.FillPath(b,p);}
 void TextDraw(Graphics g,string text,int x,int y,int w,int h,float size,Color color,bool bold=false){using(var f=new Font("Microsoft YaHei UI",size,bold?FontStyle.Bold:FontStyle.Regular,GraphicsUnit.Pixel))using(var b=new SolidBrush(color))g.DrawString(text,f,b,new RectangleF(x,y,w,h));}
 protected override void OnPaint(PaintEventArgs e){
  base.OnPaint(e);var g=e.Graphics;g.ScaleTransform(UiScale,UiScale);g.SmoothingMode=SmoothingMode.AntiAlias;
  using(var pen=new Pen(Line)){g.DrawLine(pen,56,0,56,900);g.DrawLine(pen,76,80,2324,80);}
  using(var path=Rounded(new RectangleF(12,16,32,32),8))using(var b=new SolidBrush(Card))g.FillPath(b,path);
  TextDraw(g,"V",21,20,18,24,17,Accent,true);
  TextDraw(g,"视频工作台",76,20,260,30,21,Ink,true);
  TextDraw(g,"本地创作 · 从灵感到成片",76,52,300,18,10,Muted);
  if(Page==0){
   // Quiet column separators keep four workflows readable without heavy card chrome.
   using(var pen=new Pen(Line)){g.DrawLine(pen,524,112,524,858);g.DrawLine(pen,982,112,982,858);g.DrawLine(pen,1878,112,1878,858);}
   foreach(Control c in Controls){if(!c.Visible)continue;if(c is TextBox||c is ListBox){Rectangle fr=positions[c];
    using(var fp=Rounded(new RectangleF(fr.X-6,fr.Y-6,fr.Width+12,fr.Height+12),8))using(var fb=new SolidBrush(Card)){
     g.FillPath(fb,fp);if(c.Focused)using(var fpen=new Pen(Dark?StudioPalette.GoldLine:Accent))g.DrawPath(fpen,fp);
    }
   }}
  }
  if(Page==1){Box(g,76,96,900,792);Box(g,1008,96,884,792);if(!showingBound){Box(g,96,328,860,52,8);Box(g,96,480,860,52,8);Box(g,96,598,860,52,8);}}
  if(Page==2){Box(g,76,96,1100,792);Box(g,1208,96,684,792);}
 }
 void MeasureDramaItem(object sender,MeasureItemEventArgs e){if(e.Index<0)return;string text=Convert.ToString(dramaList.Items[e.Index]);float s=UiScale;int w=Math.Max(50,(int)(dramaList.Width-24*s));var size=TextRenderer.MeasureText(text,dramaList.Font,new Size(w,0),TextFormatFlags.WordBreak);e.ItemHeight=Math.Min((int)(140*s),Math.Max((int)(24*s),size.Height+(int)(10*s)));}
 void DrawDramaItem(object sender,DrawItemEventArgs e){if(e.Index<0)return;bool sel=(e.State&DrawItemState.Selected)!=0;using(var b=new SolidBrush(sel?(Dark?StudioPalette.Selection:Color.FromArgb(234,240,234)):Card))e.Graphics.FillRectangle(b,e.Bounds);float s=UiScale;TextRenderer.DrawText(e.Graphics,Convert.ToString(dramaList.Items[e.Index]),dramaList.Font,new Rectangle(e.Bounds.X+(int)(10*s),e.Bounds.Y+(int)(5*s),e.Bounds.Width-(int)(20*s),e.Bounds.Height-(int)(8*s)),Ink,TextFormatFlags.WordBreak|TextFormatFlags.EndEllipsis);}
 void SwitchShot(){if(populating)return;int i=shotChoice.SelectedIndex;if(i<0||i>=shots.Count||i==shotIndex)return;shotIndex=i;LoadShot();}
 void RebuildShotChoices(){if(shotChoice==null)return;bool was=populating;populating=true;try{shotChoice.Items.Clear();for(int i=0;i<shots.Count;i++)shotChoice.Items.Add(new Choice(i.ToString(),shots[i].Title));if(shotIndex>=shots.Count)shotIndex=shots.Count-1;if(shotIndex<0)shotIndex=0;shotChoice.SelectedIndex=shotIndex;}finally{populating=was;}}
 async Task DramakePlan(){string story=dramaStory.Text.Trim();if(story.Length==0){SetNotice("先填写故事或点子。",true);return;}working=true;SetControls();SetNotice("正在生成剧本与分镜…",false);try{int count=6;var countChoice=dramaCount.SelectedItem as Choice;if(countChoice!=null)int.TryParse(countChoice.Id,out count);string ratio=dramaRatio.SelectedIndex==1?"16:9":(dramaRatio.SelectedIndex==2?"1:1":"9:16");var result=await Task.Run(()=>Api.Request("/dramake",new{story=story,ratio=ratio,shots=count,agent=AgentId},300000));if(closing||IsDisposed)return;dramaShotsData.Clear();foreach(object o in Json.Items(Json.Get(result,"shots"))){string p=Json.Str(o,"prompt");int sec=5;double parsed;if(double.TryParse(Convert.ToString(Json.Get(o,"seconds")),out parsed))sec=(int)parsed;if(p.Length>0)dramaShotsData.Add(new KeyValuePair<string,int>(p,sec));}dramaList.BeginUpdate();dramaList.Items.Clear();for(int i=0;i<dramaShotsData.Count;i++)dramaList.Items.Add("镜头 "+(i+1).ToString("00")+" · "+dramaShotsData[i].Value+"s · "+dramaShotsData[i].Key);dramaList.EndUpdate();string title=Json.Str(result,"title");dramaNote.Text=title.Length>0?"《"+title+"》 "+Json.Str(result,"logline"):"分镜已生成";SetNotice("已生成 "+dramaShotsData.Count+" 个分镜，检查后点击「加入分镜」。",false);}catch(Exception e){SetNotice(SafeError(e),true);}finally{working=false;SetControls();}}
 void DramakeApply(){if(dramaShotsData.Count==0){SetNotice("请先生成分镜。",true);return;}int room=12-shots.Count;if(room<1){SetNotice("分镜最多 12 个，请先删减现有分镜。",true);return;}int w=704,h=1280;if(dramaRatio.SelectedIndex==1){w=1280;h=704;}else if(dramaRatio.SelectedIndex==2){w=1024;h=1024;}int first=shots.Count,added=0;foreach(var kv in dramaShotsData){if(added>=room)break;var shot=Shot.Create(shots.Count+added+1);shot.Prompt=kv.Key;shot.Seconds=kv.Value;shot.Width=w;shot.Height=h;shot.Preset="h3_t2v";shots.Add(shot);added++;}shotIndex=first;dirty=true;SaveDraft();LoadShot();SetNotice("已加入 "+added+" 个分镜：顶部「镜头」下拉切换，逐镜交接出片。",false);}
 void DrawOutput(object sender,DrawItemEventArgs e){if(e.Index<0)return;bool selected=(e.State&DrawItemState.Selected)!=0;using(var b=new SolidBrush(selected?(Dark?StudioPalette.Selection:Color.FromArgb(234,240,234)):Card))e.Graphics.FillRectangle(b,e.Bounds);string path=Convert.ToString(outputList.Items[e.Index]);float s=UiScale;TextRenderer.DrawText(e.Graphics,Path.GetFileName(path),outputList.Font,new Rectangle(e.Bounds.X+(int)(10*s),e.Bounds.Y+(int)(8*s),e.Bounds.Width-(int)(20*s),(int)(24*s)),Ink,TextFormatFlags.EndEllipsis);TextRenderer.DrawText(e.Graphics,"MP4  ·  本机成片",outputList.Font,new Rectangle(e.Bounds.X+(int)(10*s),e.Bounds.Y+(int)(34*s),e.Bounds.Width-(int)(20*s),(int)(22*s)),Muted,TextFormatFlags.EndEllipsis);}
 static bool IsOutput(string path){try{string full=Path.GetFullPath(path),root=Path.GetFullPath(Api.OutputRoot)+Path.DirectorySeparatorChar;return full.StartsWith(root,StringComparison.OrdinalIgnoreCase)&&full.EndsWith(".mp4",StringComparison.OrdinalIgnoreCase)&&File.Exists(full);}catch{return false;}}
 void PreviewSelectedOutput(){if(outputList.SelectedItem==null||Testing)return;string path=Convert.ToString(outputList.SelectedItem);if(IsOutput(path))libraryPreview.ShowVideo(path);}
 void OpenDocs(){string f=Path.Combine(Api.BackendRoot,"VIDEO-STUDIO.txt");if(File.Exists(f))Process.Start(new ProcessStartInfo(f){UseShellExecute=true});else SetNotice("在 Agent 中使用 video-studio 的状态、准备、交接与恢复工具。",false);}
 void OpenDirectory(string path){try{if(Directory.Exists(path))Process.Start(new ProcessStartInfo("explorer.exe","\""+path+"\""){UseShellExecute=true});else SetNotice("文件夹将在首次生成后创建。",false);}catch(Exception e){SetNotice(SafeError(e),true);}}
 protected override bool ShowWithoutActivation {get{return Testing;}}
 protected override void OnHandleCreated(EventArgs e){base.OnHandleCreated(e);try{int v=Dark?1:0;DwmSetWindowAttribute(Handle,20,ref v,4);}catch{}}
 [StructLayout(LayoutKind.Sequential)] struct DpiSuggestedRect{internal int Left,Top,Right,Bottom;}
 protected override void WndProc(ref Message m){if(m.Msg==0x02E0&&IsHandleCreated&&!IsDisposed){uint dpi=(uint)((int)m.WParam&0xFFFF);var r=(DpiSuggestedRect)Marshal.PtrToStructure(m.LParam,typeof(DpiSuggestedRect));LayoutScale(FitScale(dpi));SetBounds(r.Left,r.Top,0,0,BoundsSpecified.Location);}base.WndProc(ref m);}
 protected override void Dispose(bool disposing){base.Dispose(disposing);if(disposing)foreach(var f in fonts.Values)f.Dispose();}
}
internal sealed class RefThumb : Control {
 internal bool Dark;internal float ScaleValue=1;Image thumb;string thumbPath;bool video;
 internal RefThumb(){SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer|ControlStyles.Selectable,true);Cursor=Cursors.Hand;TabStop=false;}
 int S(float n){return (int)Math.Round(n*ScaleValue);}
 internal void SetMedia(string path,bool isVideo){video=isVideo;if(path!=thumbPath){if(thumb!=null)thumb.Dispose();thumb=MediaPreview.LoadThumbnail(path,256,160);thumbPath=path;}Invalidate();}
 protected override void OnPaint(PaintEventArgs e){var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;Color fill=Dark?StudioPalette.Raised:Color.FromArgb(244,246,242);Color line=Dark?StudioPalette.Line:Color.FromArgb(222,229,220);Color muted=Dark?StudioPalette.Muted:Color.FromArgb(125,138,125);using(var p=StudioForm.Rounded(new RectangleF(1,1,Width-3,Height-3),6*ScaleValue))using(var b=new SolidBrush(fill))using(var pen=new Pen(line)){g.FillPath(b,p);g.DrawPath(pen,p);}if(thumb!=null){double ratio=Math.Min((double)(Width-6)/thumb.Width,(double)(Height-6)/thumb.Height);var box=new Rectangle((int)(3+(Width-6-thumb.Width*ratio)/2),(int)(3+(Height-6-thumb.Height*ratio)/2),Math.Max(1,(int)(thumb.Width*ratio)),Math.Max(1,(int)(thumb.Height*ratio)));g.InterpolationMode=InterpolationMode.HighQualityBicubic;g.DrawImage(thumb,box);}else{using(var pen=new Pen(muted)){float cx=Width/2f,cy=Height/2f;if(video){g.DrawLines(pen,new[]{new PointF(cx-5*ScaleValue,cy-7*ScaleValue),new PointF(cx+7*ScaleValue,cy),new PointF(cx-5*ScaleValue,cy+7*ScaleValue),new PointF(cx-5*ScaleValue,cy-7*ScaleValue)});}else{g.DrawRectangle(pen,cx-11*ScaleValue,cy-8*ScaleValue,22*ScaleValue,16*ScaleValue);g.DrawEllipse(pen,cx-6*ScaleValue,cy-3*ScaleValue,4*ScaleValue,4*ScaleValue);}}}}
 protected override void Dispose(bool disposing){if(disposing&&thumb!=null){thumb.Dispose();thumb=null;}base.Dispose(disposing);}
}
}
