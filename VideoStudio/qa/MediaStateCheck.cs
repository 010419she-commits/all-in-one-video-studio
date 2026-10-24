using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace LocalVideoStudio {
internal static class MediaStateCheck {
    static readonly List<string> report=new List<string>();
    static int passed, failed;
    static string work;
    static void Check(bool value,string label) { report.Add((value?"PASS ":"FAIL ")+label);if(value)passed++;else failed++; }
    static void ExpectError(Action action,string label) { bool rejected=false;try{action();}catch{rejected=true;}Check(rejected,label); }
    static void Pump(int ms) {var watch=Stopwatch.StartNew();while(watch.ElapsedMilliseconds<ms){Application.DoEvents();Thread.Sleep(15);}Application.DoEvents();}
    static object Field(object target,string name) {return target.GetType().GetField(name,BindingFlags.NonPublic|BindingFlags.Instance).GetValue(target);}
    [STAThread] static int Main(string[] args) {
        work=Path.Combine(Path.GetTempPath(),"VideoStudioMediaState-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(work);
        try {
            DraftChecks();
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
            MediaChecks(Path.GetFullPath(args[0]));
        } catch(Exception ex) {failed++;report.Add("FAIL unhandled: "+ex);}
        finally {try{Directory.Delete(work,true);}catch{}}
        report.Add("TOTAL "+passed+" passed / "+failed+" failed");
        File.WriteAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"media-state-check.txt"),report,Encoding.UTF8);
        return failed==0?0:1;
    }
    static void DraftChecks() {
        string path=Path.Combine(work,"storyboard.json");var shot=Shot.Create(1);
        shot.Prompt="测试镜头 🌿\n自然光，安静的窗边。";
        shot.ImagePath=Path.Combine(work,"首帧.png");shot.Validate();
        shot.WorkflowPath="prepared.api.json";shot.Phase="prepared";
        DraftStore.SaveTo(path,new[]{shot});
        var read=DraftStore.LoadFrom(path);
        Check(read.Count==1&&read[0].Prompt==shot.Prompt&&read[0].ImagePath==shot.ImagePath,"draft preserves Unicode prompt and local paths");
        Check(read[0].WorkflowPath==""&&read[0].Phase=="draft","reopen never reuses prepared workflow or queued execution");
        foreach(string phase in new[]{"done","error","failed","skipped"}) {
            var terminal=Shot.Create(1);terminal.Phase=phase;
            string terminalPath=Path.Combine(work,phase+".json");DraftStore.SaveTo(terminalPath,new[]{terminal});
            Check(terminal.Phase==phase&&DraftStore.LoadFrom(terminalPath)[0].Phase==phase,"terminal phase "+phase+" preserved across save and reopen");
        }
        foreach(string phase in new[]{"queued","running"}) {
            var active=Shot.Create(1);active.Phase=phase;active.WorkflowPath="old.api.json";
            string activePath=Path.Combine(work,phase+".json");DraftStore.SaveTo(activePath,new[]{active});
            var reopened=DraftStore.LoadFrom(activePath)[0];
            Check(reopened.Phase=="draft"&&reopened.WorkflowPath=="","interrupted "+phase+" cannot resume from persisted draft");
        }
        string json=File.ReadAllText(path);
        Check(!json.Contains("SavedInput")&&!json.Contains("api_key")&&!json.Contains("ApiKey")&&!json.Contains("Authorization"),"serialized draft contains only shot fields, no transient fingerprint or credential properties");
        shot.WorkflowPath="ready.api.json";shot.Phase="done";shot.Outputs.Add("old.mp4");shot.Prompt+="改变";shot.Validate();
        Check(shot.WorkflowPath==""&&shot.Phase=="draft"&&shot.Outputs.Count==0,"changed prompt invalidates workflow and previous output association");
        foreach(string field in new[]{"Preset","ImagePath","Width","Height","Seconds","Seed"}) {
            var item=Shot.Create(1);item.WorkflowPath="ready.api.json";item.Phase="prepared";
            switch(field){case "Preset":item.Preset="hunyuan_i2v";break;case "ImagePath":item.ImagePath="first.png";break;case "Width":item.Width=800;break;case "Height":item.Height=464;break;case "Seconds":item.Seconds=4;break;case "Seed":item.Seed=44;break;}
            item.Validate();Check(item.WorkflowPath==""&&item.Phase=="draft",field+" change invalidates prepared workflow");
        }
        var title=Shot.Create(1);title.WorkflowPath="ready.api.json";title.Phase="prepared";title.Title="新名称";title.Validate();Check(title.WorkflowPath=="ready.api.json","title-only edit preserves prepared parameters");
        shot.Prompt="保留上一版";DraftStore.SaveTo(path,new[]{shot});
        string previous=File.ReadAllText(path);shot.Prompt="最新版";DraftStore.SaveTo(path,new[]{shot});
        Check(File.ReadAllText(path+".bak")==previous,"atomic save keeps previous valid draft backup");
        File.WriteAllText(path,"{broken");read=DraftStore.LoadFrom(path);
        Check(read[0].Prompt=="保留上一版"&&DraftStore.LastLoadWarning.Contains("恢复"),"corrupt current draft restores valid backup");
        File.WriteAllText(path+".bak","{broken");read=DraftStore.LoadFrom(path);
        Check(read.Count==1&&read[0].Phase=="draft"&&DraftStore.LastLoadWarning.Length>0&&File.ReadAllText(path)=="{broken","two corrupt drafts preserved and fresh draft returned with warning");
        var invalid=Shot.Create(1);invalid.Preset="unknown";ExpectError(delegate{DraftStore.SaveTo(path,new[]{invalid});},"unknown preset rejected");
        invalid=Shot.Create(1);invalid.Width=767;ExpectError(delegate{DraftStore.SaveTo(path,new[]{invalid});},"misaligned dimensions rejected");
        invalid=Shot.Create(1);invalid.Seconds=31;ExpectError(delegate{DraftStore.SaveTo(path,new[]{invalid});},"excess duration rejected");
        invalid=Shot.Create(1);invalid.Seed=-1;ExpectError(delegate{DraftStore.SaveTo(path,new[]{invalid});},"negative seed rejected");
        invalid=Shot.Create(1);invalid.Prompt=new string('x',12001);ExpectError(delegate{DraftStore.SaveTo(path,new[]{invalid});},"oversized prompt rejected");
        invalid=Shot.Create(1);ExpectError(delegate{DraftStore.SaveTo(path,new[]{invalid,invalid});},"duplicate IDs rejected");
        ExpectError(delegate{DraftStore.SaveTo(path,new Shot[0]);},"empty storyboard rejected");
        var many=new List<Shot>();for(int i=0;i<13;i++)many.Add(Shot.Create(i+1));ExpectError(delegate{DraftStore.SaveTo(path,many);},"excess shot count rejected");
    }
    static void MediaChecks(string video) {
        using(var form=new HiddenForm())using(var media=new MediaPreview()) {
            form.Controls.Add(media);media.Bounds=new Rectangle(0,0,640,404);form.Show();Pump(100);
            var errors=new List<string>();media.Error+=delegate(string value){errors.Add(value);};
            foreach(float scale in new[]{1f,1.5f,2f,1.5f,1f})for(int i=0;i<5;i++) {
                media.UiScale=scale;media.Dark=i%2==0;
                using(var bitmap=new Bitmap(640,404))media.DrawToBitmap(bitmap,new Rectangle(0,0,640,404));
                foreach(Control control in media.Controls)Check(control.Font.Height>0,"repeated theme / DPI font remains valid");
            }
            string image=Path.Combine(work,"first.png");using(var bitmap=new Bitmap(64,32)){using(var g=Graphics.FromImage(bitmap))g.Clear(Color.SeaGreen);bitmap.Save(image);}
            media.ShowImage(image);Check(media.HasPoster&&media.MediaPath==image,"image loads as detached poster");File.Move(image,image+".moved");Check(File.Exists(image+".moved"),"image source remains unlocked");
            media.ShowVideo(video);var watch=Stopwatch.StartNew();while(!media.HasPoster&&watch.ElapsedMilliseconds<22000)Pump(50);
            Check(media.HasPoster,"CPU PyAV extracts actual MP4 poster");using(var thumb=MediaPreview.LoadThumbnail(video,100,60))Check(thumb!=null&&thumb.Width<=100&&thumb.Height<=60,"cached MP4 thumbnail loads within bounds");
            media.TogglePlayback();watch.Restart();bool played=false;double lastPosition=0;
            while(watch.ElapsedMilliseconds<10000) {Pump(75);double current=(double)Field(media,"position");if(media.IsPlaying&&current>.15){played=true;lastPosition=current;break;}if(errors.Count>0)break;}
            Check(played,"native embedded WMP enters playing state with advancing position");
            if(played){media.TogglePlayback();Pump(500);Check(!media.IsPlaying,"embedded WMP pauses");media.TogglePlayback();Pump(650);Check(media.IsPlaying&&(double)Field(media,"position")>lastPosition,"embedded WMP resumes with advancing position");}
            if(errors.Count>0)report.Add("MEDIA DETAILS "+String.Join(" | ",errors));
            int beforeFallback=errors.Count;
            typeof(MediaPreview).GetMethod("PlaybackFailed",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(media,new object[]{new InvalidOperationException("test codec failure")});
            Check(Field(media,"player")==null&&!media.IsPlaying&&(bool)Field(media,"internalFailed")&&errors.Count==beforeFallback+1,"decoder failure safely disposes internal player and reports fallback");
            Check(((Control)Field(media,"play")).Text=="外部播放"&&((Control)Field(media,"external")).Visible,"decoder failure exposes explicit system-player fallback without launching it");
            media.Clear();Check(media.MediaPath==""&&!media.IsPlaying&&!media.HasPoster&&Field(media,"player")==null,"clear stops and disposes player and poster");
            int before=errors.Count;media.ShowVideo(Path.Combine(work,"missing.mp4"));Check(errors.Count==before+1&&media.MediaPath=="","missing video reports error without changing selection");
            media.ShowVideo(video);media.Clear();Pump(500);Check(!media.HasPoster&&media.MediaPath=="","late poster worker cannot restore cleared selection");
            form.Close();
        }
    }
    sealed class HiddenForm:Form {
        internal HiddenForm(){ShowInTaskbar=false;StartPosition=FormStartPosition.Manual;Location=new Point(-20000,-20000);ClientSize=new Size(640,404);FormBorderStyle=FormBorderStyle.None;}
        protected override bool ShowWithoutActivation{get{return true;}}
        protected override CreateParams CreateParams{get{var cp=base.CreateParams;cp.ExStyle|=0x08000000;return cp;}}
    }
}
}
