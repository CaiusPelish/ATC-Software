import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("app", "routes/app.tsx"),
  route("drowsiness", "routes/drowsiness.tsx"),
  route("sim", "routes/sim.tsx"),
] satisfies RouteConfig;
