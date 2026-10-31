import { Clapperboard, Library } from "lucide-react";
import { Link } from "react-router-dom";
import { RecentSessions } from "../components/RecentSessions";

const cards = [
  {
    to: "/auto-edit",
    kicker: "Mode A",
    title: "AI Auto-Edit",
    subtitle: "Upload your lesson and let the existing AI editing pipeline find relevant footage, create captions, and prepare your video.",
    points: ["One raw lesson video", "Existing search, checks, and ranking stay in place", "Captions, music, branding, and outro in the render"],
    action: "Start Auto-Edit",
    icon: Clapperboard,
    accent: "bg-blue",
  },
  {
    to: "/my-broll",
    kicker: "Mode B",
    title: "Edit with My B-Roll",
    subtitle: "Bring your own footage. Describe each clip and let AI match it to the right moments in your lesson.",
    points: ["Your clips are the only cutaways", "A one-line description for every file", "No online footage is added in this mode"],
    action: "Use My B-Roll",
    icon: Library,
    accent: "bg-teal",
  },
];

export function HomePage() {
  return (
    <div className="grid gap-8">
      <section className="max-w-3xl">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-200">Educational video production</p>
        <h1 className="mt-3 font-serif text-4xl leading-tight text-white sm:text-5xl">
          Turn a raw lesson into a polished short.
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-7 text-mist">
          Choose how footage should be chosen. After a session is prepared, AI processing writes a transcript and an editing plan. A video is rendered only when you review the plan and choose Render Video.
        </p>
      </section>
      <section className="grid gap-4 lg:grid-cols-2" aria-label="Editing workflows">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <Link
              key={card.to}
              to={card.to}
              className="group flex flex-col rounded-3xl border border-white/10 bg-white p-6 text-ink shadow-[0_20px_50px_rgba(0,0,0,0.18)] transition hover:-translate-y-0.5"
            >
              <span className={`grid h-12 w-12 place-items-center rounded-2xl text-white ${card.accent}`}>
                <Icon aria-hidden="true" size={22} />
              </span>
              <p className="mt-5 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">{card.kicker}</p>
              <h2 className="mt-1 font-serif text-3xl">{card.title}</h2>
              <p className="mt-3 text-sm leading-6 text-slate-700">{card.subtitle}</p>
              <ul className="mt-4 grid gap-1 text-sm text-slate-600">
                {card.points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
              <span className="mt-6 inline-flex w-fit rounded-full bg-ink px-4 py-2 text-sm text-white group-hover:bg-panel">
                {card.action}
              </span>
            </Link>
          );
        })}
      </section>
      <RecentSessions />
    </div>
  );
}
