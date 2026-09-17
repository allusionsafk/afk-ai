namespace AFKLocalAI.App;

/// <summary>What the Home screen is showing. Only the reducer produces one.</summary>
public enum HomeKind
{
    Checking,
    Ready,
    Starting,
    Stopped,
    Degraded,
    Failed,
    RepairNeeded,
    Unknown
}

/// <summary>Evidence that chat answered, from a qualify-mode engine status.</summary>
public sealed record ChatQualification(DateTimeOffset At, string? Model, string ChatUrl, bool? OnboardingRequired);

public sealed record HomeModel(
    HomeKind Kind,
    ProductStatus? Latest,
    ChatQualification? Qualified,
    DateTimeOffset? ObservedAt,
    string Detail,
    bool NeedsQualification)
{
    public static HomeModel Initial { get; } =
        new(HomeKind.Checking, null, null, null, "AFK AI is checking its services.", NeedsQualification: true);
}

public abstract record Observation(DateTimeOffset At);

/// <summary>A schema-2 status the engine reported.</summary>
public sealed record StatusObserved(DateTimeOffset At, ProductStatus Status) : Observation(At);

/// <summary>The engine could not be asked: runtime missing or corrupt, launch or parse failure, timeout.</summary>
public sealed record EngineUnavailable(DateTimeOffset At, string Detail, bool RepairNeeded) : Observation(At);

/// <summary>
/// Turns engine evidence into what Home shows. The rules that keep Home honest:
/// </summary>
/// <remarks>
/// <list type="number">
/// <item>READY is shown only from a qualify-mode status whose inference passed
/// and whose chat URL is this PC's loopback chat.</item>
/// <item>A liveness status can KEEP a qualification (same model, not expired,
/// nothing regressed in between) but never create one.</item>
/// <item>Any regressed, failed, or unobservable status drops the qualification
/// immediately: a stale READY cannot survive a runtime failure.</item>
/// </list>
/// This is not a second readiness interpretation - it never inspects services.
/// It decides only how long engine evidence stays valid.
/// </remarks>
public static class HomeReducer
{
    public static readonly TimeSpan QualificationLifetime = TimeSpan.FromMinutes(10);

    public static HomeModel Reduce(HomeModel previous, Observation observation) => observation switch
    {
        EngineUnavailable unavailable => new HomeModel(
            unavailable.RepairNeeded ? HomeKind.RepairNeeded : HomeKind.Unknown,
            null, null, unavailable.At, unavailable.Detail, NeedsQualification: true),
        StatusObserved observed => ReduceStatus(previous, observed),
        _ => previous
    };

    private static HomeModel ReduceStatus(HomeModel previous, StatusObserved observed)
    {
        var status = observed.Status;
        var qualify = status.Mode == ProductStatus.ModeQualify;

        if (status.State == ProductStatus.States.Ready)
        {
            var proven = qualify && status.Chat.Ready && status.Qualification.Inference == "passed" &&
                ChatGate.IsLoopbackChatUrl(status.Chat.Url);
            if (!proven)
            {
                // The engine contract says this cannot happen. If it does, the
                // honest answer is "we do not know", not "ready".
                return new HomeModel(HomeKind.Unknown, status, null, observed.At,
                    "AFK AI reported an inconsistent status. Check again or create a diagnostics report.",
                    NeedsQualification: true);
            }
            var qualification = new ChatQualification(observed.At, status.Model, status.Chat.Url!, status.Chat.OnboardingRequired);
            return new HomeModel(HomeKind.Ready, status, qualification, observed.At, status.Message, NeedsQualification: false);
        }

        if (status.State == ProductStatus.States.Live)
        {
            var kept = previous.Qualified;
            if (!qualify && kept is not null && previous.Kind == HomeKind.Ready &&
                kept.Model == status.Model && observed.At - kept.At <= QualificationLifetime && observed.At >= kept.At)
            {
                var onboarding = status.Chat.OnboardingRequired ?? kept.OnboardingRequired;
                return new HomeModel(HomeKind.Ready, previous.Latest ?? status, kept with { OnboardingRequired = onboarding },
                    observed.At, previous.Detail, NeedsQualification: false);
            }
            return new HomeModel(HomeKind.Checking, status, null, observed.At,
                "AFK AI is running. Chat has not been checked yet.", NeedsQualification: true);
        }

        var kind = status.State switch
        {
            ProductStatus.States.Starting => HomeKind.Starting,
            ProductStatus.States.Stopped => HomeKind.Stopped,
            ProductStatus.States.Degraded => HomeKind.Degraded,
            ProductStatus.States.NotInstalled => HomeKind.RepairNeeded,
            ProductStatus.States.Failed when status.NextAction == "repair" => HomeKind.RepairNeeded,
            ProductStatus.States.Failed => HomeKind.Failed,
            _ => HomeKind.Unknown
        };
        return new HomeModel(kind, status, null, observed.At, status.Message, NeedsQualification: true);
    }
}

public enum ChatGateDecision
{
    /// <summary>Qualification is fresh: a cheap liveness probe must still pass.</summary>
    ProbeLiveness,
    /// <summary>No fresh qualification: run the full check before routing.</summary>
    Requalify
}

public static class ChatGate
{
    /// <summary>Only this PC's own chat, over loopback, at its root.</summary>
    public static bool IsLoopbackChatUrl(string? url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)) return false;
        return uri.Scheme == Uri.UriSchemeHttp &&
               uri.Host is "127.0.0.1" or "localhost" &&
               uri.Port == 3000 &&
               uri.AbsolutePath == "/" &&
               string.IsNullOrEmpty(uri.Query) &&
               string.IsNullOrEmpty(uri.Fragment) &&
               string.IsNullOrEmpty(uri.UserInfo);
    }

    /// <summary>True only when Home may route someone to chat right now.</summary>
    public static bool CanOpen(HomeModel model, DateTimeOffset now) =>
        model.Kind == HomeKind.Ready &&
        model.Qualified is { } qualification &&
        now >= qualification.At &&
        now - qualification.At <= HomeReducer.QualificationLifetime &&
        IsLoopbackChatUrl(qualification.ChatUrl);

    /// <summary>What must happen before the browser is opened.</summary>
    public static ChatGateDecision BeforeOpen(HomeModel model, DateTimeOffset now) =>
        CanOpen(model, now) ? ChatGateDecision.ProbeLiveness : ChatGateDecision.Requalify;
}

/// <summary>How often Home re-observes, by what it is showing.</summary>
public static class RefreshPolicy
{
    public static TimeSpan LivenessInterval(HomeKind kind) => kind switch
    {
        HomeKind.Starting or HomeKind.Checking => TimeSpan.FromSeconds(5),
        HomeKind.Ready => TimeSpan.FromSeconds(30),
        _ => TimeSpan.FromSeconds(60)
    };

    public static readonly TimeSpan ActivationStaleness = TimeSpan.FromSeconds(15);

    /// <summary>
    /// Minimum gap between AUTOMATIC full checks. A full check can load the model;
    /// without this, a model that keeps failing its test prompt would be reloaded
    /// on every liveness cycle. A person pressing a button is never throttled.
    /// </summary>
    public static readonly TimeSpan AutoQualifyBackoff = TimeSpan.FromMinutes(5);

    /// <summary>Kinds where a periodic cheap probe should escalate to a full check.</summary>
    public static bool ShouldQualify(HomeModel model) =>
        model.Kind == HomeKind.Checking && model.NeedsQualification &&
        model.Latest?.State == ProductStatus.States.Live;

    public static bool ShouldAutoQualify(HomeModel model, DateTimeOffset? lastAutoQualify, DateTimeOffset now) =>
        ShouldQualify(model) && (lastAutoQualify is null || now - lastAutoQualify.Value >= AutoQualifyBackoff);
}

/// <summary>The one next step Home offers, from the engine's fixed allowlist.</summary>
public enum HomeAction { OpenChat, Start, Repair, Check, Wait, Diagnostics }

public sealed record HomeView(string Headline, string Detail, HomeAction Primary, string PrimaryLabel, bool PrimaryEnabled, bool CanStop);

public static class HomePresenter
{
    public static HomeView Present(HomeModel model, DateTimeOffset now)
    {
        var onboarding = model.Qualified?.OnboardingRequired == true;
        var headline = model.Kind switch
        {
            HomeKind.Ready => "Your local AI is ready",
            HomeKind.Checking => "Checking AFK AI",
            HomeKind.Starting => "AFK AI is starting",
            HomeKind.Stopped => "AFK AI is stopped",
            HomeKind.Degraded => "AFK AI needs attention",
            HomeKind.Failed => "AFK AI can't run right now",
            HomeKind.RepairNeeded => "AFK AI needs repair",
            _ => "AFK AI couldn't check its status"
        };
        var detail = string.IsNullOrWhiteSpace(model.Detail) ? "" : model.Detail;
        if (model.Kind == HomeKind.Ready && onboarding)
            detail = "Chat is ready. The first account you create becomes the owner of this PC's chat.";

        var action = model.Kind switch
        {
            HomeKind.Ready => HomeAction.OpenChat,
            HomeKind.Checking => HomeAction.Check,
            HomeKind.Unknown => HomeAction.Check,
            HomeKind.RepairNeeded => HomeAction.Repair,
            _ => ActionFor(model.Latest?.NextAction)
        };
        if (action == HomeAction.OpenChat && !ChatGate.CanOpen(model, now)) action = HomeAction.Check;

        var label = action switch
        {
            HomeAction.OpenChat => onboarding ? "Open Chat and create account" : "Open Chat",
            HomeAction.Start => "Start AFK AI",
            HomeAction.Repair => "Repair AFK AI",
            HomeAction.Check => model.Kind == HomeKind.Checking ? "Check chat" : "Check again",
            HomeAction.Wait => "Please wait…",
            _ => "Create diagnostics report"
        };
        var canStop = model.Kind is HomeKind.Ready or HomeKind.Checking or HomeKind.Starting or HomeKind.Degraded;
        return new HomeView(headline, detail, action, label, action != HomeAction.Wait, canStop);
    }

    /// <summary>Unknown engine actions fall back to diagnostics, never to chat.</summary>
    public static HomeAction ActionFor(string? nextAction) => nextAction switch
    {
        "start" => HomeAction.Start,
        "repair" => HomeAction.Repair,
        "check" => HomeAction.Check,
        "wait" => HomeAction.Wait,
        _ => HomeAction.Diagnostics
    };
}
