import Nav from "@/components/Nav";
import Hero from "@/components/Hero";
import Stats from "@/components/Stats";
import GetStarted from "@/components/GetStarted";
import Workers from "@/components/Workers";
import Features from "@/components/Features";
import HowItWorks from "@/components/HowItWorks";
import Terminal from "@/components/Terminal";
import Graph from "@/components/Graph";
import Bench from "@/components/Bench";
import Pipelines from "@/components/Pipelines";
import Extend from "@/components/Extend";
import Benchmarks from "@/components/Benchmarks";
import Safety from "@/components/Safety";
import Alternatives from "@/components/Alternatives";
import Faq from "@/components/Faq";
import Community from "@/components/Community";
import Footer from "@/components/Footer";

export default function Page() {
  return (
    <main>
      <Nav />
      <Hero />
      <Stats />
      <Terminal />
      <Graph />
      <Bench />
      <HowItWorks />
      <GetStarted />
      <Workers />
      <Features />
      <Pipelines />
      <Extend />
      <Benchmarks />
      <Safety />
      <Alternatives />
      <Faq />
      <Community />
      <Footer />
    </main>
  );
}
