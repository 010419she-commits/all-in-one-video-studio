// Offline regression for reference-aware video model selection.
// Compile with the five current production sources and
// /main:LocalVideoStudio.ReferenceModeTests /target:exe.
// Never calls Show, Program.Main, a submit action, or the real draft path.
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

namespace LocalVideoStudio {
internal static class ReferenceModeTests {
 const BindingFlags Private=BindingFlags.Instance|BindingFlags.NonPublic;
 static readonly string[] Families={"hunyuan","ltx","ltx25","h3"};
 static int assertions,fakeRequests;

 static void Check(bool condition,string name){assertions++;if(!condition)throw new InvalidOperationException("FAILED: "+name);}
 static T Field<T>(StudioForm form,string name){return (T)typeof(StudioForm).GetField(name,Private).GetValue(form);}
 static Shot Current(StudioForm form){return (Shot)typeof(StudioForm).GetProperty("Current",Private).GetValue(form,null);}
 static void Invoke(StudioForm form,string name){typeof(StudioForm).GetMethod(name,Private).Invoke(form,null);}
 static string Selected(ChoiceBox box){var choice=box.SelectedItem as Choice;return choice==null?"":choice.Id;}
 static int ChoiceIndex(ChoiceBox box,string id){for(int i=0;i<box.Items.Count;i++){var choice=box.Items[i] as Choice;if(choice!=null&&choice.Id==id)return i;}throw new InvalidOperationException("Missing choice "+id);}
 static Shot NewShot(string preset){var shot=Shot.Create(1);shot.Preset=preset;shot.Prompt="Offline reference mode fixture";shot.Content="Fixture motion";return shot;}
 static void SetShot(StudioForm form,Shot shot){Field<List<Shot>>(form,"shots")[0]=shot;typeof(StudioForm).GetField("dirty",Private).SetValue(form,false);Invoke(form,"LoadShot");}
 static Dictionary<string,object> NoRequests(string path,object body,int timeout){fakeRequests++;throw new InvalidOperationException("API forbidden in reference-mode QA");}

 static void PureModeTests(string work){
  foreach(string family in Families){
   var image=NewShot(family+"_t2v");image.ImagePaths.Add(Path.Combine(work,"first.png"));image.ImagePaths.Add(Path.Combine(work,"second.png"));
   string fingerprint=image.InputFingerprint(),id=image.Id,prompt=image.Prompt;
   Check(image.NormalizeReferenceMode(),family+" image converts");
   Check(image.Preset==family+"_i2v",family+" image uses matching family");
   Check(image.ImagePaths.Count==2&&Path.GetFileName(image.ImagePaths[0])=="first.png"&&Path.GetFileName(image.ImagePaths[1])=="second.png",family+" preserves ordered images");
   Check(image.Id==id&&image.Prompt==prompt,family+" preserves identity and text");
   Check(image.InputFingerprint()!=fingerprint,family+" changed mode changes input fingerprint");
   Check(!image.NormalizeReferenceMode(),family+" image normalization idempotent");

   var video=NewShot(family+"_t2v");video.VideoPath=Path.Combine(work,"reference.mp4");string originalVideo=video.VideoPath;
   Check(video.NormalizeReferenceMode()&&video.Preset==family+"_i2v",family+" video converts");
   Check(video.VideoPath==originalVideo&&video.ImagePaths.Count==0,family+" preserves video without adding images");
   Check(!video.NormalizeReferenceMode(),family+" video normalization idempotent");

   var text=NewShot(family+"_t2v");
   Check(!text.NormalizeReferenceMode()&&text.Preset==family+"_t2v",family+" text without media unchanged");
   var alreadyImage=NewShot(family+"_i2v");alreadyImage.ImagePaths.Add(Path.Combine(work,"image.png"));
   Check(!alreadyImage.NormalizeReferenceMode()&&alreadyImage.Preset==family+"_i2v",family+" existing image mode unchanged");
   var missingImage=NewShot(family+"_i2v");
   Check(!missingImage.NormalizeReferenceMode()&&missingImage.Preset==family+"_i2v",family+" empty image mode not silently downgraded");
  }
  foreach(string preset in new[]{"musetalk_lipsync","cosyvoice_tts","cosyvoice_lipsync","unknown_t2v"}){
   var shot=NewShot(preset);shot.ImagePaths.Add(Path.Combine(work,"reference.png"));shot.VideoPath=Path.Combine(work,"reference.mp4");
   Check(!shot.NormalizeReferenceMode()&&shot.Preset==preset,preset+" not remapped");
  }
 }

 static void FormTests(string work){
  using(var form=new StudioForm(true,NoRequests)){
   IntPtr handle=form.Handle;
   Check(handle!=IntPtr.Zero&&form.Testing&&!form.Visible,"hidden test form without Show");
   var box=Field<ChoiceBox>(form,"preset");var ids=new HashSet<string>(StringComparer.Ordinal);
   foreach(object item in box.Items){var choice=item as Choice;Check(choice!=null&&ids.Add(choice.Id),"unique model choice ID");}
   foreach(string family in Families){
    ChoiceIndex(box,family+"_t2v");ChoiceIndex(box,family+"_i2v");
    foreach(bool useVideo in new[]{false,true}){
     var legacy=NewShot(family+"_t2v");
     if(useVideo)legacy.VideoPath=Path.Combine(work,"legacy.mp4");else legacy.ImagePaths.Add(Path.Combine(work,"legacy.png"));
     string reference=useVideo?legacy.VideoPath:legacy.ImagePath;
     SetShot(form,legacy);
     Check(Current(form).Preset==family+"_i2v"&&Selected(box)==family+"_i2v",family+" legacy "+(useVideo?"video":"image")+" restored with matching UI");
     Check((useVideo?Current(form).VideoPath:Current(form).ImagePath)==reference,family+" restored reference preserved");
     Check(Field<bool>(form,"dirty"),family+" restored correction marked for draft save");

     // Switching to the family's text mode with a retained reference must
     // correct both the model object and visible selection immediately.
     legacy.WorkflowPath=Path.Combine(work,"obsolete-workflow.json");legacy.Phase="prepared";legacy.Outputs.Add(Path.Combine(work,"obsolete.mp4"));
     box.SelectedIndex=ChoiceIndex(box,family+"_t2v");
     Check(legacy.Preset==family+"_i2v"&&Selected(box)==family+"_i2v",family+" incompatible selection immediately synchronized");
     Check(legacy.WorkflowPath==""&&legacy.Phase=="draft"&&legacy.Outputs.Count==0,family+" editor invalidates stale generated state");
     Invoke(form,"EditCurrent");
     Check(legacy.Preset==family+"_i2v"&&Selected(box)==family+"_i2v",family+" repeated edit remains synchronized");

     legacy.ImagePaths.Clear();legacy.VideoPath="";
     box.SelectedIndex=ChoiceIndex(box,family+"_t2v");
     Check(legacy.Preset==family+"_t2v"&&Selected(box)==family+"_t2v",family+" text mode selectable after removing reference");
    }
   }
   foreach(string tool in new[]{"musetalk_lipsync","cosyvoice_tts","cosyvoice_lipsync"}){
    var shot=NewShot(tool);shot.VideoPath=Path.Combine(work,"tool.mp4");SetShot(form,shot);Invoke(form,"EditCurrent");
    Check(Current(form).Preset==tool&&Selected(box)==tool,tool+" selection preserved");
   }
   Check(!Field<System.Windows.Forms.Timer>(form,"poll").Enabled&&!Field<System.Windows.Forms.Timer>(form,"saveDraft").Enabled,"no status or disk-save timers started");
   Check(!form.Visible,"form stayed hidden");
  }
 }

 static void DraftTests(string work){
  // A real legacy envelope in a temporary folder exercises migration,
  // persistence and recovery without opening or writing the user's draft.
  foreach(string family in Families){
   string path=Path.Combine(work,family+"-storyboard.json");
   Check(!String.Equals(Path.GetFullPath(path),Path.GetFullPath(DraftStore.FilePath),StringComparison.OrdinalIgnoreCase),"fixture is separate from user draft");
   var legacy=NewShot(family+"_t2v");legacy.ImagePaths.Add(Path.Combine(work,"draft-first.png"));legacy.ImagePaths.Add(Path.Combine(work,"draft-second.png"));legacy.Seconds=10;legacy.Width=704;legacy.Height=1280;
   var envelope=new DraftStore.DraftEnvelope{Version=1,Shots=new List<Shot>{legacy}};
   string original=Json.Serializer.Serialize(envelope);File.WriteAllText(path,original,new UTF8Encoding(false));
   var loaded=DraftStore.LoadFrom(path);
   Check(loaded.Count==1&&loaded[0].Id==legacy.Id&&loaded[0].ImagePaths.Count==2,family+" legacy draft loaded with references");
   using(var form=new StudioForm(true,NoRequests)){
    SetShot(form,loaded[0]);
    Check(Current(form).Preset==family+"_i2v"&&Selected(Field<ChoiceBox>(form,"preset"))==family+"_i2v",family+" draft migration visible and stored in shot");
    DraftStore.SaveTo(path,new[]{Current(form)});
   }
   Check(File.Exists(path+".bak")&&File.ReadAllText(path+".bak",Encoding.UTF8)==original,family+" atomic save preserves original legacy backup");
   var reopened=DraftStore.LoadFrom(path);
   Check(reopened.Count==1&&reopened[0].Preset==family+"_i2v",family+" migrated mode persisted on reopen");
   Check(reopened[0].Id==legacy.Id&&reopened[0].Prompt==legacy.Prompt&&reopened[0].Content==legacy.Content&&reopened[0].Seconds==10&&reopened[0].Width==704&&reopened[0].Height==1280,family+" non-mode draft fields preserved");
   Check(reopened[0].ImagePaths.Count==2&&reopened[0].ImagePaths[0]==legacy.ImagePaths[0]&&reopened[0].ImagePaths[1]==legacy.ImagePaths[1],family+" image ordering persisted");
   Check(!reopened[0].NormalizeReferenceMode(),family+" persisted migration is idempotent");
  }
 }

 [STAThread] static int Main(){
  string temp=Path.GetFullPath(Path.GetTempPath());
  string work=Path.Combine(temp,"VideoStudioReferenceMode-"+Guid.NewGuid().ToString("N"));
  try{
   Directory.CreateDirectory(work);
   PureModeTests(work);
   Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
   FormTests(work);DraftTests(work);
   Check(fakeRequests==0,"zero injected API attempts");
   Console.WriteLine("PASS: "+assertions+" assertions; four video families; image/video normalization; hidden UI synchronization; unique choices; temporary draft migration and backups; injected API=0; Show=0; GPU=0.");
   return 0;
  }catch(Exception error){
   while(error is TargetInvocationException&&error.InnerException!=null)error=error.InnerException;
   Console.WriteLine("FAIL: "+error.GetType().Name+": "+error.Message);return 1;
  }finally{
   string resolved=Path.GetFullPath(work);
   string prefix=temp.TrimEnd(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar)+Path.DirectorySeparatorChar;
   if(resolved.StartsWith(prefix,StringComparison.OrdinalIgnoreCase)&&Path.GetFileName(resolved).StartsWith("VideoStudioReferenceMode-",StringComparison.Ordinal)&&Directory.Exists(resolved))Directory.Delete(resolved,true);
  }
 }
}
}
