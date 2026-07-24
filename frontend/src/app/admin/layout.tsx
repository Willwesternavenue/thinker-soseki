import { redirect } from "next/navigation";
import { getUserWithProfile } from "@/lib/supabase/server";
import { AdminNav } from "@/components/admin-nav";

export default async function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const auth = await getUserWithProfile();
  if (!auth) redirect("/login");
  if (auth.profile.role !== "admin") redirect("/chat");

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-stone-200 bg-white">
        <AdminNav />
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
        {children}
      </main>
    </div>
  );
}
