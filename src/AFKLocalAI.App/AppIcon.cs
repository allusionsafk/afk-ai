namespace AFKLocalAI.App;

public static class AppIcon
{
    public static Icon Create()
    {
        using var asset = typeof(AppIcon).Assembly.GetManifestResourceStream("AFKLocalAI.App.Assets.afk-ai.ico")
            ?? throw new InvalidOperationException("AFK application icon resource is missing.");
        using var icon = new Icon(asset);
        return (Icon)icon.Clone();
    }
}
