// Offline native regression for per-shot compute engine selection.
// Compile with the five production sources and
// /main:LocalVideoStudio.ComputePathTests /target:exe /platform:x64.
// Never shows a window, submits a task, calls Program.Main, or uses the real draft.
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

namespace LocalVideoStudio {
internal static class ComputePathTests {
 const BindingFlags Private=BindingFlags.Instance|BindingFlags.NonPublic;
 static int assertions,requests;
 static void Check(bool condition,string name){assertions++;if(!condition)throw new InvalidOperationException("FAILED: "+name);}
 static T Field<T>(StudioForm form,string name){return (T)typeof(StudioForm).GetField(name,Private).GetValue(form);}
 static void SetField(StudioForm form,string name,object value){typeof(StudioForm).GetField(name,Private).SetValue(form,value);}
 static Shot Current(StudioForm form){return (Shot)typeof(StudioForm).GetProperty("Current",Private).GetValue(form,null);}
 static void Invoke(StudioForm form,string name){typeof(StudioForm).GetMethod(name,Private).Invoke(form,null);}
 static string Selected(ChoiceBox box){var choice=box.SelectedItem as Choice;return choice==null?"":choice.Id;}
 static int Index(ChoiceBox box,string id){for(int i=0;i<box.Items.Count;i++){var c=box.Items[i] as Choice;if(c!=null&&c.Id==id)return i;}throw new InvalidOperationException("Missing choice "+id);}
 static Dictionary<string,object> Obj(params object[] pairs){var result=new Dictionary<string,object>();for(int i=0;i<pairs.Length;i+=2)result.Add((string)pairs[i],pairs[i+1]);return result;}
 static Dictionary<string,object> NoRequest(string path,object body,int timeout){requests++;throw new InvalidOperationException("Network forbidden in compute-path QA");}
 static void Reject(Action action,string name){bool rejected=false;try{action();}catch(InvalidOperationException){rejected=true;}Check(rejected,name);}

 static Shot Fixture(string work,string computePath,bool turbo,int number=1){
  var shot=Shot.Create(number);shot.Preset="h3_i2v";shot.ComputePath=computePath;shot.Fast=turbo;
  shot.Prompt="Offline compute engine fixture "+number;shot.Content="Keep motion and framing";shot.Instruct="Fixture instruction";
  shot.ImagePaths.Add(Path.Combine(work,"reference-"+number+".png"));shot.ImagePaths.Add(Path.Combine(work,"reference-extra-"+number+".png"));
  shot.AudioPath=Path.Combine(work,"audio-"+number+".wav");shot.Width=704;shot.Height=1280;shot.Seconds=10;shot.Seed=100+number;
  shot.Validate();return shot;
 }
 static string OtherInputs(Shot shot){
  return Json.Serializer.Serialize(new object[]{shot.Id,shot.Title,shot.Prompt,shot.Content,shot.Preset,shot.ImagePaths,shot.VideoPath,shot.AudioPath,shot.Instruct,shot.Fast,shot.Width,shot.Height,shot.Seconds,shot.Seed});
 }
 static void Put(StudioForm form,Shot shot){
  var shots=Field<List<Shot>>(form,"shots");shots.Clear();shots.Add(shot);SetField(form,"shotIndex",0);Invoke(form,"LoadShot");
 }
 static void Prepared(Shot shot,string work){shot.WorkflowPath=Path.Combine(work,"obsolete-workflow.json");shot.Phase="prepared";shot.Outputs.Add(Path.Combine(work,"obsolete.mp4"));shot.SavedInput=shot.InputFingerprint();}

 static void StateTests(string work){
  Check(Shot.Create(1).ComputePath=="hd","new shots default to memory-saving hd engine");
  foreach(bool turbo in new[]{false,true})foreach(string path in new[]{"fast","hd"}){
   var shot=Fixture(work,path,turbo);string original=OtherInputs(shot);shot.Validate();
   Check(shot.ComputePath==path&&shot.Fast==turbo,"valid path independent of Turbo "+path+"/"+turbo);
   string before=shot.InputFingerprint();Prepared(shot,work);shot.ComputePath=path=="fast"?"hd":"fast";
   Check(shot.InputFingerprint()!=before,"engine selection participates in fingerprint "+path+"/"+turbo);
   shot.Validate();
   Check(shot.WorkflowPath==""&&shot.Phase=="draft"&&shot.Outputs.Count==0,"engine change invalidates stale graph "+path+"/"+turbo);
   Check(OtherInputs(shot)==original,"engine validation preserves all model inputs "+path+"/"+turbo);
  }
  foreach(string invalid in new[]{"turbo","HD","../fast",""}){
   var shot=Fixture(work,"fast",true);shot.ComputePath=invalid;
   Reject(delegate{shot.Validate();},"invalid engine rejected: "+invalid);
  }
 }

 static void DraftTests(string work){
  foreach(bool turbo in new[]{false,true})foreach(string path in new[]{"fast","hd"}){
   var shot=Fixture(work,path,turbo);string stable=OtherInputs(shot);
   string file=Path.Combine(work,"draft-"+path+"-"+turbo+".json");
   Check(!String.Equals(Path.GetFullPath(file),Path.GetFullPath(DraftStore.FilePath),StringComparison.OrdinalIgnoreCase),"test draft is isolated");
   DraftStore.SaveTo(file,new[]{shot});var reopened=DraftStore.LoadFrom(file);
   Check(reopened.Count==1&&reopened[0].ComputePath==path&&reopened[0].Fast==turbo,"saved engine and Turbo reopen independently "+path+"/"+turbo);
   Check(OtherInputs(reopened[0])==stable,"reopened non-engine inputs preserved "+path+"/"+turbo);
  }
  // Remove the new property from a serialized fixture to reproduce a real
  // pre-feature envelope; never substitute the user's on-disk storyboard.
  var oldShot=Fixture(work,"hd",false);var envelope=new DraftStore.DraftEnvelope{Version=1,Shots=new List<Shot>{oldShot}};
  var plain=Json.Obj(Json.Serializer.DeserializeObject(Json.Serializer.Serialize(envelope)));
  foreach(object item in Json.Items(Json.Get(plain,"Shots")))Json.Obj(item).Remove("ComputePath");
  string legacy=Json.Serializer.Serialize(plain);Check(legacy.IndexOf("\"ComputePath\":",StringComparison.Ordinal)<0,"legacy fixture omits new field");
  string legacyPath=Path.Combine(work,"legacy.json");File.WriteAllText(legacyPath,legacy,new UTF8Encoding(false));
  var loaded=DraftStore.LoadFrom(legacyPath);
  Check(loaded.Count==1&&loaded[0].Id==oldShot.Id,"old JSON loads actual fixture rather than fallback shot");
  Check(loaded[0].ComputePath=="hd"&&!loaded[0].Fast,"old JSON defaults to memory-saving engine only; preserves Turbo false");
  Check(OtherInputs(loaded[0])==OtherInputs(oldShot),"old JSON preserves model inputs");
  DraftStore.SaveTo(legacyPath,loaded);
  Check(File.ReadAllText(legacyPath+".bak",Encoding.UTF8)==legacy,"legacy backup kept verbatim");
  var reopenedLegacy=DraftStore.LoadFrom(legacyPath);
  Check(reopenedLegacy.Count==1&&reopenedLegacy[0].ComputePath=="hd"&&!reopenedLegacy[0].Fast,"migrated memory-saving default persists on reopen");
 }

 static Dictionary<string,object> IdleStatus(){
  return Obj("service","video-studio","api_version",1,
   "settings",Obj("key_saved",false,"agent","kimi_api","kimi_model","fixture-model","kimi_base_url","https://fixture.invalid/v1"),
   "controller",Obj("connected",true,"busy",false,"comfy_running",false,"can_accept_batch",true,"phase","idle"),
   "catalog",new object[0],"agents",new object[0],"tasks",new object[0],"outputs",new object[0],"image_outputs",new object[0]);
 }
 static void FormTests(string work){
  using(var form=new StudioForm(true,NoRequest)){
   IntPtr handle=form.Handle;Check(handle!=IntPtr.Zero&&form.Testing&&!form.Visible,"hidden Testing form without Show");
   var box=Field<ChoiceBox>(form,"computePath");Check(box.Items.Count==2,"exactly two compute paths");
   Check(Index(box,"fast")!=Index(box,"hd"),"distinct fast and hd IDs");
   Check(((Choice)box.Items[Index(box,"fast")]).Label.IndexOf("快速",StringComparison.Ordinal)>=0&&((Choice)box.Items[Index(box,"hd")]).Label.IndexOf("高清",StringComparison.Ordinal)>=0,"visible labels identify paths");
   foreach(bool turbo in new[]{false,true})foreach(string path in new[]{"fast","hd"}){
    var shot=Fixture(work,path,turbo);string stable=OtherInputs(shot);Put(form,shot);
    Check(Selected(box)==path&&Current(form).ComputePath==path,"LoadShot selects stored path "+path+"/"+turbo);
    Check(OtherInputs(shot)==stable,"LoadShot preserves model parameters "+path+"/"+turbo);
    Prepared(shot,work);string next=path=="fast"?"hd":"fast";box.SelectedIndex=Index(box,next);
    Check(shot.ComputePath==next&&Selected(box)==next,"selection edits current shot "+path+"/"+turbo);
    Check(shot.WorkflowPath==""&&shot.Phase=="draft"&&shot.Outputs.Count==0,"selection invalidates prepared state "+path+"/"+turbo);
    Check(OtherInputs(shot)==stable&&shot.Fast==turbo,"selection preserves resolution, media and Turbo "+path+"/"+turbo);
    Invoke(form,"EditCurrent");Check(shot.ComputePath==next&&OtherInputs(shot)==stable,"EditCurrent reads selected engine only "+path+"/"+turbo);
    form.ApplyStatus(IdleStatus());
    Check(shot.ComputePath==next&&Selected(box)==next&&OtherInputs(shot)==stable,"status update does not reset engine or model inputs "+path+"/"+turbo);
   }
   // Two shots keep their own engine and independent Turbo value while the
   // existing shot selector drives the actual SwitchShot path.
   var first=Fixture(work,"fast",true,1);var second=Fixture(work,"hd",false,2);
   var shots=Field<List<Shot>>(form,"shots");shots.Clear();shots.Add(first);shots.Add(second);SetField(form,"shotIndex",0);Invoke(form,"LoadShot");
   var selector=Field<ChoiceBox>(form,"shotChoice");
   Check(Current(form)==first&&Selected(box)=="fast","first shot engine loaded");
   selector.SelectedIndex=1;Check(Current(form)==second&&Selected(box)=="hd"&&!second.Fast,"second shot engine and Turbo loaded independently");
   box.SelectedIndex=Index(box,"fast");Check(second.ComputePath=="fast"&&first.ComputePath=="fast"&&!second.Fast&&first.Fast,"editing second engine leaves first parameters alone");
   selector.SelectedIndex=0;box.SelectedIndex=Index(box,"hd");
   Check(Current(form)==first&&first.ComputePath=="hd"&&second.ComputePath=="fast"&&first.Fast&&!second.Fast,"engine edits isolated per shot");
   selector.SelectedIndex=1;Check(Current(form)==second&&Selected(box)=="fast","switching back restores second engine");
   string multi=Path.Combine(work,"two-shots.json");DraftStore.SaveTo(multi,shots);var opened=DraftStore.LoadFrom(multi);
   Check(opened.Count==2&&opened[0].ComputePath=="hd"&&opened[1].ComputePath=="fast"&&opened[0].Fast&&!opened[1].Fast,"both per-shot engine choices persist together");
   Check(!Field<System.Windows.Forms.Timer>(form,"poll").Enabled&&!Field<System.Windows.Forms.Timer>(form,"saveDraft").Enabled,"no polling or real-draft timers");
   Check(!form.Visible,"form remained hidden throughout tests");
  }
 }

 [STAThread] static int Main(){
  string temp=Path.GetFullPath(Path.GetTempPath());string work=Path.Combine(temp,"VideoStudioComputePath-"+Guid.NewGuid().ToString("N"));
  try{
   Directory.CreateDirectory(work);StateTests(work);DraftTests(work);
   Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);FormTests(work);
   Check(requests==0,"zero injected requests");
   Console.WriteLine("PASS: "+assertions+" assertions; fast/hd state and drafts; legacy JSON; hidden UI; unchanged sampling and resolution; status refresh; isolated shots; API=0; Show=0; GPU=0.");return 0;
  }catch(Exception error){
   while(error is TargetInvocationException&&error.InnerException!=null)error=error.InnerException;
   Console.WriteLine("FAIL: "+error.GetType().Name+": "+error.Message);return 1;
  }finally{
   string resolved=Path.GetFullPath(work),prefix=temp.TrimEnd(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar)+Path.DirectorySeparatorChar;
   if(resolved.StartsWith(prefix,StringComparison.OrdinalIgnoreCase)&&Path.GetFileName(resolved).StartsWith("VideoStudioComputePath-",StringComparison.Ordinal)&&Directory.Exists(resolved))Directory.Delete(resolved,true);
  }
 }
}
}
