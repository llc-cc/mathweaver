import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
    index("routes/landing.tsx"),
    route("workspace", "routes/home.tsx"),
    route("admin/users", "routes/AdminUsers.tsx"),
] satisfies RouteConfig;
