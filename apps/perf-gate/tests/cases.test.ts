import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, test } from "vitest";
import { DEFAULT_LOCAL_REPLAY_IDS, DEFAULT_REPO_REPLAY_IDS } from "../src/config/defaults.js";
import { prepareCases } from "../src/cases/loadReplay.js";

const tempRoots: string[] = [];

async function makeTempRoot(): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), "probeplanner-cases-"));
  tempRoots.push(root);
  return root;
}

async function writeReplay(pathname: string, label: string, targetKeys = ["CA1"]): Promise<void> {
  await mkdir(path.dirname(pathname), { recursive: true });
  await writeFile(pathname, JSON.stringify({
    request: {
      targetKeys,
      params: { algorithm: "greedy" },
      clinicalContext: { replayCase: label },
    },
  }), "utf8");
}

afterEach(async () => {
  while (tempRoots.length > 0) {
    const root = tempRoots.pop();
    if (root) await rm(root, { recursive: true, force: true });
  }
});

describe("prepareCases", () => {
  test("writes logical manifest source paths instead of external filesystem paths", async () => {
    const rootDir = await makeTempRoot();
    const sourceProject = path.join(rootDir, "external-probe-planner");
    const localReplayDir = path.join(rootDir, "user-home-replay");
    await Promise.all([
      ...DEFAULT_REPO_REPLAY_IDS.map((id) =>
        writeReplay(path.join(sourceProject, "replay", `${id}.json`), `repo-${id.slice(0, 8)}`),
      ),
      ...DEFAULT_LOCAL_REPLAY_IDS.map((id) =>
        writeReplay(path.join(localReplayDir, `${id}.json`), `local-${id.slice(0, 8)}`),
      ),
    ]);

    const manifest = await prepareCases({ rootDir, sourceProject, localReplayDir });

    expect(manifest.sourceProject).toBe("cases/requests");
    for (const item of manifest.cases) {
      if (item.source === "repo") {
        expect(item.sourcePath).toBe(`repo-replay:${item.id}`);
      } else if (item.source === "local") {
        expect(item.sourcePath).toBe(`local-replay:${item.id}`);
      } else if (item.source === "adversarial") {
        expect(item.sourcePath).toMatch(/^generated:sdf-adversarial:/);
      } else {
        expect(item.sourcePath).toMatch(/^generated:(paper_replay|stress|ablation):/);
      }
      expect(item.sourcePath).not.toContain(rootDir);
      expect(item.sourcePath).not.toContain("external-probe-planner");
      expect(item.sourcePath).not.toContain("user-home-replay");
    }
  });

  test("filters non-atlas targets from a supplied native region map", async () => {
    const rootDir = await makeTempRoot();
    const sourceProject = path.join(rootDir, "external-probe-planner");
    const localReplayDir = path.join(rootDir, "user-home-replay");
    const regionMapPath = path.join(rootDir, "region_map_v2.json");
    await writeFile(regionMapPath, JSON.stringify({
      entries: [
        { canonicalAcronym: "CA1", aliases: ["CA1"], id: 1 },
        { canonicalAcronym: "MOs", aliases: ["MOs"], id: 2 },
      ],
    }), "utf8");
    await Promise.all([
      ...DEFAULT_REPO_REPLAY_IDS.map((id) =>
        writeReplay(path.join(sourceProject, "replay", `${id}.json`), `repo-${id.slice(0, 8)}`, ["CA1", "PFC"]),
      ),
      ...DEFAULT_LOCAL_REPLAY_IDS.map((id) =>
        writeReplay(path.join(localReplayDir, `${id}.json`), `local-${id.slice(0, 8)}`, ["MOs", "PTLp"]),
      ),
    ]);

    const manifest = await prepareCases({
      rootDir,
      sourceProject,
      localReplayDir,
      nativeRegionMapPath: regionMapPath,
    });

    const replayCases = manifest.cases.filter((item) => item.source === "repo" || item.source === "local");
    expect(replayCases).toHaveLength(10);
    for (const item of replayCases) {
      expect(item.targetKeys.every((key) => key === "CA1" || key === "MOs")).toBe(true);
      expect(item.excludedTargetKeys?.length).toBe(1);
      expect(item.excludedTargetKeys?.[0]).toMatch(/PFC|PTLp/);
    }
  });
});
