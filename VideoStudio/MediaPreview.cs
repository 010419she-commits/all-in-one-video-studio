using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace LocalVideoStudio {
// A self-contained, CPU-only poster / native media player. Never starts an AI engine.
internal sealed class MediaPreview : Panel {
    const string WmpClsid = "6BF52A52-394A-11d3-B153-00C04F79FAA6";
    readonly PreviewButton play = new PreviewButton(), external = new PreviewButton();
    readonly Timer timer = new Timer();
    readonly Dictionary<float, Font> buttonFonts = new Dictionary<float, Font>();
    Image poster;
    PlayerHost player;
    bool dark, isVideo, playing, disposed, internalFailed;
    float uiScale = 1;
    int generation;
    string mediaPath = "", note = "选择首帧或成片进行预览";
    double position, duration;
    DateTime playbackStarted;
    Rectangle timeline;
    public event Action<string> StatusChanged;
    public event Action<string> Error;
    internal MediaPreview() {
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        TabStop = false;
        AccessibleName = "视频与首帧预览";
        play.Text = "播放"; external.Text = "系统播放器";
        play.Click += delegate { TogglePlayback(); };
        external.Click += delegate { OpenExternal(); };
        Controls.Add(play); Controls.Add(external);
        timer.Interval = 250; timer.Tick += delegate { PollPlayback(); };
        ApplyStyle(); LayoutChildren();
    }
    internal bool Dark { get { return dark; } set { dark = value; ApplyStyle(); Invalidate(); } }
    internal float UiScale { get { return uiScale; } set { uiScale = Math.Max(.75f, Math.Min(4, value)); ApplyStyle(); LayoutChildren(); Invalidate(); } }
    internal string MediaPath { get { return mediaPath; } }
    internal bool IsPlaying { get { return playing; } }
    internal bool HasPoster { get { return poster != null; } }
    int S(float v) { return (int)Math.Round(v * uiScale); }
    Color Ink { get { return dark ? StudioPalette.Ink : Color.FromArgb(46, 54, 49); } }
    Color Muted { get { return dark ? StudioPalette.Muted : Color.FromArgb(115, 124, 116); } }
    void ApplyStyle() {
        BackColor = dark ? StudioPalette.Panel : Color.FromArgb(243, 245, 242);
        Font font;
        if (!buttonFonts.TryGetValue(uiScale, out font)) {
            font = new Font("Microsoft YaHei UI", 8.5f * uiScale, FontStyle.Regular, GraphicsUnit.Point);
            buttonFonts.Add(uiScale, font);
        }
        foreach(PreviewButton b in new [] { play, external }) {
            b.Dark = dark; b.ScaleValue = uiScale; b.BackColor = BackColor;
            // WinForms may retain its existing Font when a value-equal Font is assigned.
            // Keep every font we own alive until all child controls have been disposed.
            b.Font = font; b.Invalidate();
        }
    }
    protected override void OnResize(EventArgs e) { base.OnResize(e); LayoutChildren(); }
    void LayoutChildren() {
        int h = S(44), bottom = Math.Max(0, Height - h);
        play.SetBounds(S(12), bottom + S(7), S(62), S(30));
        external.SetBounds(Math.Max(S(86), Width - S(112)), bottom + S(7), S(100), S(30));
        timeline = new Rectangle(S(88), bottom + S(20), Math.Max(0, Width - S(300)), S(4));
        if (player != null) player.SetBounds(1, 1, Math.Max(1, Width - 2), Math.Max(1, Height - h - 1));
        play.Visible = external.Visible = isVideo;
    }
    internal void Clear() {
        generation++; StopPlayer(); mediaPath = ""; isVideo = false;
        position = duration = 0; internalFailed = false;
        ReplacePoster(null); note = "选择首帧或成片进行预览"; LayoutChildren(); Invalidate();
    }
    internal void ShowImage(string path) {
        if (String.IsNullOrWhiteSpace(path)) { Clear(); return; }
        try {
            string full = Path.GetFullPath(path);
            // Decode a detached bitmap before replacing the current image. The source file is never locked.
            Image next = LoadDetached(full, 2560, 2560);
            generation++; StopPlayer(); mediaPath = full; isVideo = false; internalFailed = false;
            position = duration = 0; ReplacePoster(next); note = "首帧预览";
            LayoutChildren(); Invalidate(); Notify("首帧已载入");
        } catch (Exception ex) { Fail("无法预览这张图片：" + Short(ex.Message)); }
    }
    static bool IsVideoFile(string path) {
        string ext = Path.GetExtension(path);
        return String.Equals(ext, ".mp4", StringComparison.OrdinalIgnoreCase) || String.Equals(ext, ".mov", StringComparison.OrdinalIgnoreCase)
            || String.Equals(ext, ".webm", StringComparison.OrdinalIgnoreCase) || String.Equals(ext, ".mkv", StringComparison.OrdinalIgnoreCase);
    }
    internal static bool PlayableInPlace(string path) { return String.Equals(Path.GetExtension(path), ".mp4", StringComparison.OrdinalIgnoreCase); }
    internal void ShowVideo(string path) {
        if (String.IsNullOrWhiteSpace(path)) { Clear(); return; }
        string full;
        try {
            full = Path.GetFullPath(path);
            if (!File.Exists(full)) throw new FileNotFoundException("视频文件不存在");
            if (!IsVideoFile(full)) throw new InvalidOperationException("请选择视频成片");
        } catch (Exception ex) { Fail("无法预览视频：" + Short(ex.Message)); return; }
        if (String.Equals(full, mediaPath, StringComparison.OrdinalIgnoreCase) && isVideo) return;
        generation++; int request = generation; StopPlayer();
        mediaPath = full; isVideo = true; internalFailed = false; position = duration = 0;
        ReplacePoster(null); note = "正在读取视频首帧…"; LayoutChildren(); Invalidate();
        // Worker lifetime is bounded; all process output is consumed to avoid pipe deadlocks.
        Task.Factory.StartNew(delegate {
            Image next = null; string error = null;
            try { string cache = EnsurePoster(full); next = LoadDetached(cache, 2560, 2560); }
            catch (Exception ex) { error = Short(ex.Message); }
            if (disposed || !IsHandleCreated) { if(next != null) next.Dispose(); return; }
            try { BeginInvoke((MethodInvoker)delegate {
                if (disposed || request != generation) { if(next != null) next.Dispose(); return; }
                ReplacePoster(next); note = next == null ? "首帧暂不可用，仍可播放视频" : "成片预览";
                Invalidate(); Notify(error == null ? "视频已载入，点击播放" : "首帧读取未完成：" + error);
            }); } catch (InvalidOperationException) { if(next != null) next.Dispose(); }
        });
    }
    void ReplacePoster(Image next) { Image old = poster; poster = next; if(old != null) old.Dispose(); }
    static Image LoadDetached(string path, int width, int height) {
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        using (var original = Image.FromStream(stream, true, true)) {
            double ratio = Math.Min(1, Math.Min((double)width / original.Width, (double)height / original.Height));
            var result = new Bitmap(Math.Max(1,(int)(original.Width*ratio)), Math.Max(1,(int)(original.Height*ratio)));
            using (Graphics g = Graphics.FromImage(result)) { g.InterpolationMode=InterpolationMode.HighQualityBicubic; g.DrawImage(original, 0, 0, result.Width, result.Height); }
            return result;
        }
    }
    // Ownership of the returned image belongs to the caller. Does not spawn a process.
    internal static Image LoadThumbnail(string path, int width, int height) {
        if(String.IsNullOrWhiteSpace(path)) return null;
        try {
            string source = IsVideoFile(path) ? PosterPath(path) : path;
            return File.Exists(source) ? LoadDetached(source, width, height) : null;
        } catch { return null; }
    }
    static string PosterPath(string path) {
        var f = new FileInfo(path);
        string identity = f.FullName.ToUpperInvariant() + "\n" + f.Length + "\n" + f.LastWriteTimeUtc.Ticks;
        string hash;
        using(var sha=SHA256.Create()) hash=BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(identity))).Replace("-","").ToLowerInvariant();
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LocalVideoStudio", "posters", hash + ".png");
    }
    static string Quote(string value) { return "\"" + value.Replace("\\", "\\").Replace("\"", "\\\"") + "\""; }
    static string EnsurePoster(string path) {
        string target = PosterPath(path); if(File.Exists(target)) return target;
        string python = Api.PythonPath;
        if(Path.IsPathRooted(python) && !File.Exists(python)) throw new FileNotFoundException("预览组件尚未就绪");
        Directory.CreateDirectory(Path.GetDirectoryName(target));
        string code = "import av,sys,os,uuid; c=av.open(sys.argv[1]); s=c.streams.video[0]; s.thread_count=2; f=next(c.decode(s)); im=f.to_image(); im.thumbnail((1920,1080)); p=sys.argv[2]+'.'+uuid.uuid4().hex+'.tmp'; im.save(p,format='PNG'); c.close(); os.replace(p,sys.argv[2])";
        var psi = new ProcessStartInfo(python, "-X utf8 -c " + Quote(code) + " " + Quote(path) + " " + Quote(target)) {
            UseShellExecute=false, CreateNoWindow=true, WindowStyle=ProcessWindowStyle.Hidden,
            RedirectStandardOutput=true, RedirectStandardError=true
        };
        using (var p = new Process { StartInfo=psi }) {
            StringBuilder stderr = new StringBuilder();
            p.OutputDataReceived += delegate { };
            p.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if(e.Data != null) lock(stderr) { if(stderr.Length < 4000) stderr.AppendLine(e.Data); } };
            p.Start(); p.BeginOutputReadLine(); p.BeginErrorReadLine();
            if(!p.WaitForExit(20000)) { try { p.Kill(); } catch { } throw new TimeoutException("首帧读取超时"); }
            p.WaitForExit();
            if(p.ExitCode != 0 || !File.Exists(target)) throw new InvalidOperationException("无法解码视频首帧");
        }
        return target;
    }
    internal void TogglePlayback() {
        if(!isVideo || String.IsNullOrEmpty(mediaPath)) return;
        if(!PlayableInPlace(mediaPath)) { OpenExternal(); return; }
        try {
            if(internalFailed) { OpenExternal(); return; }
            if(player == null) {
                player = new PlayerHost(WmpClsid); ((ISupportInitialize)player).BeginInit();
                Controls.Add(player); player.Visible=true; LayoutChildren(); ((ISupportInitialize)player).EndInit(); player.CreateControl();
                object ocx=player.Instance;
                Set(ocx,"uiMode","none"); Set(ocx,"stretchToFit",true); Set(ocx,"enableContextMenu",false);
                object settings=Get(ocx,"settings"); Set(settings,"autoStart",false);
                Set(ocx,"URL",mediaPath); player.BringToFront();
                Call(Get(ocx,"controls"),"play"); playbackStarted=DateTime.UtcNow; timer.Start();
                play.Text="暂停"; Notify("正在载入播放器…");
            } else {
                object controls=Get(player.Instance,"controls");
                int state=Convert.ToInt32(Get(player.Instance,"playState"));
                if(state==3) Call(controls,"pause"); else { Call(controls,"play"); playbackStarted=DateTime.UtcNow; }
            }
        } catch (Exception ex) { PlaybackFailed(ex); }
    }
    void PollPlayback() {
        if(player == null || disposed) return;
        try {
            object ocx=player.Instance; int state=Convert.ToInt32(Get(ocx,"playState"));
            playing=state==3; play.Text=playing ? "暂停" : "播放"; play.Invalidate();
            object controls=Get(ocx,"controls"); position=Convert.ToDouble(Get(controls,"currentPosition"));
            object current=Get(ocx,"currentMedia"); if(current != null) duration=Convert.ToDouble(Get(current,"duration"));
            if((DateTime.UtcNow-playbackStarted).TotalSeconds>5 && state!=3 && state!=2 && state!=8) {
                object errors=Get(ocx,"error");
                if(errors != null && Convert.ToInt32(Get(errors,"errorCount"))>0) throw new InvalidOperationException("系统内置解码器无法打开此视频");
            }
            Invalidate(new Rectangle(0, Math.Max(0,Height-S(44)), Width, S(44)));
        } catch(Exception ex) { PlaybackFailed(ex); }
    }
    void PlaybackFailed(Exception ex) {
        StopPlayer(); internalFailed=true; play.Text="外部播放"; note="可在系统播放器中查看成片";
        Invalidate(); Fail("内置播放不可用，请点“系统播放器”："+Short(ex.GetBaseException().Message));
    }
    void StopPlayer() {
        timer.Stop(); playing=false; play.Text="播放";
        if(player != null) {
            try { object o=player.Instance; Call(Get(o,"controls"),"stop"); Call(o,"close"); } catch { }
            Controls.Remove(player); player.Dispose(); player=null;
        }
    }
    void OpenExternal() {
        try {
            if(!File.Exists(mediaPath)) throw new FileNotFoundException("视频文件不存在");
            if(player != null) { try { Call(Get(player.Instance,"controls"),"pause"); } catch { } }
            Process.Start(new ProcessStartInfo(mediaPath) { UseShellExecute=true }); Notify("已打开系统播放器");
        } catch(Exception ex) { Fail("无法打开系统播放器："+Short(ex.Message)); }
    }
    static object Get(object target,string name) { return target.GetType().InvokeMember(name,BindingFlags.GetProperty,null,target,null,CultureInfo.InvariantCulture); }
    static void Set(object target,string name,object value) { target.GetType().InvokeMember(name,BindingFlags.SetProperty,null,target,new object[]{value},CultureInfo.InvariantCulture); }
    static void Call(object target,string name) { target.GetType().InvokeMember(name,BindingFlags.InvokeMethod,null,target,null,CultureInfo.InvariantCulture); }
    protected override void OnMouseDown(MouseEventArgs e) {
        base.OnMouseDown(e);
        if(player!=null && duration>0 && timeline.Width>0 && e.X>=timeline.Left && e.X<=timeline.Right && Math.Abs(e.Y-timeline.Top)<S(14)) {
            try { Set(Get(player.Instance,"controls"),"currentPosition",duration*Math.Max(0,Math.Min(1,(double)(e.X-timeline.Left)/timeline.Width))); } catch(Exception ex) { Fail("无法跳转播放位置："+Short(ex.Message)); }
        }
    }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e); Graphics g=e.Graphics; g.SmoothingMode=SmoothingMode.AntiAlias;
        Rectangle area = new Rectangle(1,1,Math.Max(1,Width-2), Math.Max(1,Height-(isVideo?S(44):0)-2));
        if(poster!=null) {
            double ratio=Math.Min((double)area.Width/poster.Width,(double)area.Height/poster.Height);
            var box=new Rectangle(area.Left+(area.Width-(int)(poster.Width*ratio))/2,area.Top+(area.Height-(int)(poster.Height*ratio))/2,(int)(poster.Width*ratio),(int)(poster.Height*ratio));
            g.InterpolationMode=InterpolationMode.HighQualityBicubic; g.DrawImage(poster,box);
        } else {
            int cx=area.Left+area.Width/2, cy=area.Top+area.Height/2-S(21);
            using(Pen p=new Pen(dark?StudioPalette.Muted:Color.FromArgb(172,184,174),S(1.3f))) {
                g.DrawRectangle(p,cx-S(25),cy-S(17),S(50),S(34));
                g.DrawLines(p,new Point[]{new Point(cx-S(5),cy-S(7)),new Point(cx+S(7),cy),new Point(cx-S(5),cy+S(7)),new Point(cx-S(5),cy-S(7))});
            }
            using(var f=new Font("Microsoft YaHei UI",10*uiScale,FontStyle.Regular,GraphicsUnit.Point))
                TextRenderer.DrawText(g,note,f,new Rectangle(S(10),cy+S(31),Width-S(20),S(30)),Muted,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
        }
        using(var path=StudioForm.Rounded(new RectangleF(.5f,.5f,Math.Max(1,Width-1),Math.Max(1,Height-1)),12*uiScale))using(Pen p=new Pen(dark?StudioPalette.Line:Color.FromArgb(222,227,220)))g.DrawPath(p,path);
        if(isVideo) {
            int y=Height-S(44);
            using(Pen p=new Pen(dark?StudioPalette.Line:Color.FromArgb(222,227,220)))g.DrawLine(p,0,y,Width,y);
            if(timeline.Width>0) {
                using(var b=new SolidBrush(dark?StudioPalette.Line:Color.FromArgb(210,218,211)))g.FillRectangle(b,timeline);
                using(var b=new SolidBrush(dark?StudioPalette.Gold:Ink))g.FillRectangle(b,timeline.Left,timeline.Top,(int)(timeline.Width*(duration>0?Math.Max(0,Math.Min(1,position/duration)):0)),timeline.Height);
                using(var f=new Font("Segoe UI",8*uiScale,FontStyle.Regular,GraphicsUnit.Point))
                    TextRenderer.DrawText(g,Time(position)+" / "+Time(duration),f,new Rectangle(timeline.Right+S(10),y+S(10),S(80),S(25)),Muted,TextFormatFlags.VerticalCenter|TextFormatFlags.HorizontalCenter);
            }
        }
    }
    static string Time(double seconds) { if(Double.IsNaN(seconds)||Double.IsInfinity(seconds)||seconds<0)seconds=0; int v=(int)Math.Min(Int32.MaxValue,seconds); return (v/60).ToString("00")+":"+(v%60).ToString("00"); }
    static string Short(string message) { return message.Length>180?message.Substring(0,180):message; }
    void Notify(string value) { if(StatusChanged!=null)StatusChanged(value); }
    void Fail(string value) { if(Error!=null)Error(value); }
    protected override void Dispose(bool disposing) {
        bool releaseFonts = disposing && !disposed;
        if(releaseFonts) { disposed=true; generation++; StopPlayer(); timer.Dispose(); ReplacePoster(null); }
        base.Dispose(disposing);
        if(releaseFonts) { foreach(Font font in buttonFonts.Values) font.Dispose(); buttonFonts.Clear(); }
    }
    sealed class PlayerHost : AxHost {
        internal PlayerHost(string clsid):base(clsid) { }
        internal object Instance { get { return GetOcx(); } }
    }
    sealed class PreviewButton : Button {
        internal bool Dark; internal float ScaleValue=1;
        bool hover;
        internal PreviewButton() { FlatStyle=FlatStyle.Flat; FlatAppearance.BorderSize=0; Cursor=Cursors.Hand; SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true); }
        protected override void OnMouseEnter(EventArgs e) { hover=true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover=false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnPaint(PaintEventArgs e) {
            e.Graphics.Clear(BackColor);
            if(hover)using(var path=StudioForm.Rounded(new RectangleF(0,0,Width-1,Height-1),8*ScaleValue))using(var b=new SolidBrush(Dark?StudioPalette.Raised:Color.FromArgb(229,235,228)))e.Graphics.FillPath(b,path);
            Color ink=Dark?StudioPalette.Ink:Color.FromArgb(46,54,49);
            TextRenderer.DrawText(e.Graphics,Text,Font,ClientRectangle,ink,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
            if(Focused&&ShowFocusCues)ControlPaint.DrawFocusRectangle(e.Graphics,Rectangle.Inflate(ClientRectangle,-3,-3),ink,BackColor);
        }
    }
}
}