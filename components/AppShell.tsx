import Nav from "./Nav";
import Footer from "./Footer";

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Nav />
      <main id="main-content" className="min-w-0 flex-1">
        {children}
      </main>
      <Footer />
    </>
  );
}
