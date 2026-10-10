import type { Route } from "./+types/home";
import { Link } from "react-router";
import { Welcome } from "../welcome/welcome";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "New React Router App" },
    { name: "description", content: "Welcome to React Router!" },
  ];
}

export default function Home() {
  return (
    <>
      <Welcome />
      <div className="pb-10 text-center">
        <Link
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          to="/app"
        >
          Open ATC simulation
        </Link>
      </div>
    </>
  );
}
