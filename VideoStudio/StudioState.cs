using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

namespace LocalVideoStudio {
internal sealed class Shot {
 public string Id { get; set; }
 public string Title { get; set; }
 public string Prompt { get; set; }
 public string Content { get; set; }
 public string Preset { get; set; }
 List<string> imagePaths=new List<string>();
 public List<string> ImagePaths { get { return imagePaths; } set { imagePaths=value??new List<string>(); } }
 public string ImagePath { get { return imagePaths.Count>0?imagePaths[0]:""; } set { if(string.IsNullOrEmpty(value))imagePaths.Clear();else if(imagePaths.Count==0)imagePaths.Add(value);else imagePaths[0]=value; } }
 public string VideoPath { get; set; }
 public string AudioPath { get; set; }
 public string Instruct { get; set; }
 public bool Fast { get; set; }
 string computePath="hd";
 public string ComputePath { get { return computePath; } set { computePath=value??"hd"; } }
 public int Width { get; set; }
 public int Height { get; set; }
 public int Seconds { get; set; }
 public long Seed { get; set; }
 public string WorkflowPath { get; set; }
 public string Phase { get; set; }
 public List<string> Outputs { get; set; }
 internal string SavedInput;

 internal static Shot Create(int number) {
  var shot=new Shot { Id=Guid.NewGuid().ToString("N"), Title="镜头 "+Math.Max(1,number).ToString("00"), Prompt="", Content="", Preset="hunyuan_t2v", ImagePath="", VideoPath="", AudioPath="", Instruct="", Fast=true, Width=768, Height=448, Seconds=5, Seed=42, WorkflowPath="", Phase="draft", Outputs=new List<string>() };
  shot.SavedInput=shot.InputFingerprint();
  return shot;
 }
 internal void Invalidate() { WorkflowPath=""; Phase="draft"; Outputs=new List<string>(); SavedInput=InputFingerprint(); }
 internal bool NormalizeReferenceMode() {
  if(imagePaths.Count==0&&string.IsNullOrEmpty(VideoPath))return false;
  switch(Preset) {
   case "hunyuan_t2v":Preset="hunyuan_i2v";return true;
   case "ltx_t2v":Preset="ltx_i2v";return true;
   case "ltx25_t2v":Preset="ltx25_i2v";return true;
   case "h3_t2v":Preset="h3_i2v";return true;
   default:return false;
  }
 }
 internal string InputFingerprint() {
  // Length prefixes prevent a delimiter inside the prompt from colliding with another field.
  var data=new StringBuilder();
  foreach(string value in new[]{Prompt,Content,Preset,string.Join("",ImagePaths),VideoPath,AudioPath,Instruct,Fast.ToString(),Width.ToString(),Height.ToString(),Seconds.ToString(),Seed.ToString()}) {
   string v=value??"";data.Append(v.Length).Append(':').Append(v);
  }
  data.Append(ComputePath.Length).Append(':').Append(ComputePath);
  using(var hash=SHA256.Create())return Convert.ToBase64String(hash.ComputeHash(Encoding.UTF8.GetBytes(data.ToString())));
 }
 internal void Validate() {
  Guid id;
  if(!Guid.TryParse(Id,out id))Id=Guid.NewGuid().ToString("N");
  Title=(Title??"").Trim();if(Title.Length==0)Title="未命名镜头";if(Title.Length>120)Title=Title.Substring(0,120);
  Prompt=Prompt??"";if(Prompt.Length>12000)throw new InvalidOperationException("每个镜头的画面描述最多 12000 字。");
  Content=Content??"";if(Content.Length>4000)throw new InvalidOperationException("每个镜头的视频内容最多 4000 字。");
  Preset=Preset??"hunyuan_t2v";
  if(ComputePath!="fast"&&ComputePath!="hd")throw new InvalidOperationException("请选择快速路径或高清路径。");
  if(Preset!="hunyuan_t2v"&&Preset!="hunyuan_i2v"&&Preset!="ltx_t2v"&&Preset!="ltx_i2v"&&Preset!="ltx25_t2v"&&Preset!="ltx25_i2v"&&Preset!="h3_t2v"&&Preset!="h3_i2v"&&Preset!="musetalk_lipsync"&&Preset!="cosyvoice_tts"&&Preset!="cosyvoice_lipsync")throw new InvalidOperationException("镜头使用了未知的视频模型。");
  bool tool=Preset=="musetalk_lipsync"||Preset=="cosyvoice_tts"||Preset=="cosyvoice_lipsync";
  WorkflowPath=WorkflowPath??"";VideoPath=VideoPath??"";AudioPath=AudioPath??"";Instruct=Instruct??"";
  imagePaths.RemoveAll(p=>string.IsNullOrEmpty(p));
  if(imagePaths.Count>6)imagePaths=imagePaths.GetRange(0,6);
  if(WorkflowPath.Length>32767||VideoPath.Length>32767||AudioPath.Length>32767)throw new InvalidOperationException("镜头文件路径过长。");
  foreach(string p in imagePaths)if(p.Length>32767)throw new InvalidOperationException("镜头文件路径过长。");
  if(Instruct.Length>2000)throw new InvalidOperationException("情绪指令最多 2000 字。");
  if(VideoPath.Length>0){string ext=Path.GetExtension(VideoPath).ToLowerInvariant();if(ext!=".mp4"&&ext!=".mov"&&ext!=".webm"&&ext!=".mkv")throw new InvalidOperationException("参考视频仅支持 MP4、MOV、WebM 或 MKV。");}
  if(AudioPath.Length>0){string ext=Path.GetExtension(AudioPath).ToLowerInvariant();if(ext!=".wav"&&ext!=".mp3"&&ext!=".m4a"&&ext!=".flac"&&ext!=".ogg"&&ext!=".aac")throw new InvalidOperationException("音频仅支持 WAV、MP3、M4A、FLAC、OGG、AAC。");}
  if(!tool){
   int grid=Preset.StartsWith("ltx",StringComparison.Ordinal)||Preset.StartsWith("h3",StringComparison.Ordinal)?32:16;
   if(Width<256||Width>3840||Height<256||Height>3840||Width%grid!=0||Height%grid!=0||Math.Max(Width,Height)>2*Math.Min(Width,Height))throw new InvalidOperationException("镜头画幅尺寸无效。");
   if(Seconds<1||Seconds>30)throw new InvalidOperationException("镜头时长需要在 1 至 30 秒之间。");
  }
  if(Seed<0)throw new InvalidOperationException("随机种子需要填写非负整数。");
  if(Outputs==null)Outputs=new List<string>();
  if(Outputs.Count>100)Outputs=Outputs.GetRange(0,100);
  Phase=Phase??"draft";
  if(Phase!="draft"&&Phase!="prepared"&&Phase!="queued"&&Phase!="running"&&Phase!="done"&&Phase!="error"&&Phase!="failed"&&Phase!="skipped")Phase="draft";
  string now=InputFingerprint();
  if(SavedInput!=null&&SavedInput!=now)Invalidate();
  SavedInput=now;
 }
}

internal static class DraftStore {
 internal static readonly string FilePath=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"VideoStudio","storyboard.json");
 internal static string LastLoadWarning="";
 static readonly object Gate=new object();
 internal static List<Shot> Load() {return LoadFrom(FilePath);}
 internal static List<Shot> LoadFrom(string path) {
  lock(Gate) {
   LastLoadWarning="";
   foreach(string candidate in new[]{path,path+".bak"}) {
    if(!File.Exists(candidate))continue;
    try {
     if(new FileInfo(candidate).Length>2000000)throw new InvalidDataException("Draft too large");
     var serializer=new JavaScriptSerializer{MaxJsonLength=2000000,RecursionLimit=64};
     var envelope=serializer.Deserialize<DraftEnvelope>(File.ReadAllText(candidate,Encoding.UTF8));
     if(envelope==null||envelope.Version!=1||envelope.Shots==null||envelope.Shots.Count<1||envelope.Shots.Count>12)throw new InvalidDataException("Unsupported draft");
     var ids=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
     foreach(var shot in envelope.Shots) {
      if(shot==null)throw new InvalidDataException("Empty shot");
      shot.Validate();if(!ids.Add(shot.Id)){shot.Id=Guid.NewGuid().ToString("N");ids.Add(shot.Id);}
      // Prepared graphs are checked again after reopening; opening a draft never starts generation.
      shot.WorkflowPath="";
      if(shot.Phase=="prepared"||shot.Phase=="queued"||shot.Phase=="running")shot.Phase="draft";
      shot.SavedInput=shot.InputFingerprint();
     }
     if(candidate!=path)LastLoadWarning="已从上一次保存恢复分镜草稿。";
     return envelope.Shots;
    } catch {LastLoadWarning="分镜草稿暂时无法读取，已保留原文件。";}
   }
   return new List<Shot>{Shot.Create(1)};
  }
 }
 internal static void Save(IList<Shot> shots) {SaveTo(FilePath,shots);}
 internal static void SaveTo(string path,IList<Shot> shots) {
  lock(Gate) {
   if(shots==null||shots.Count<1||shots.Count>12)throw new InvalidOperationException("分镜数量需要在 1 至 12 个之间。");
   var ids=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
   foreach(var shot in shots) {
    if(shot==null)throw new InvalidOperationException("分镜草稿中存在空镜头。");
    shot.Validate();if(!ids.Add(shot.Id))throw new InvalidOperationException("镜头编号重复，请重新添加该镜头。");
   }
   var serializer=new JavaScriptSerializer{MaxJsonLength=2000000,RecursionLimit=64};
   string data=serializer.Serialize(new DraftEnvelope{Version=1,Shots=new List<Shot>(shots)});
   string folder=Path.GetDirectoryName(Path.GetFullPath(path));Directory.CreateDirectory(folder);
   string temp=Path.Combine(folder,".storyboard-"+Guid.NewGuid().ToString("N")+".tmp");
   try {
    using(var file=new FileStream(temp,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
     byte[] bytes=new UTF8Encoding(false).GetBytes(data);file.Write(bytes,0,bytes.Length);file.Flush(true);
    }
    if(File.Exists(path))File.Replace(temp,path,path+".bak",true);else File.Move(temp,path);
   } finally {if(File.Exists(temp))File.Delete(temp);}
  }
 }
 public sealed class DraftEnvelope {public int Version{get;set;}public List<Shot> Shots{get;set;}}
}
}
