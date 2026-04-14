import React, { useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { AlertCircle, CheckCircle2, Link2, MousePointerClick, Image as ImageIcon, FileText, Search, Save, Upload, Plus, Trash2 } from "lucide-react";

type ErrorSource = "axe" | "wave" | "manual";

type ErrorItem = {
  id: string;
  source: ErrorSource;
  type: string;
  targetXpath?: string;
  assignedComponentId?: string | null;
  notes?: string;
};

type ComponentItem = {
  id: string;
  autoType: string;
  manualType: string;
  manualSubtype: string;
  xpath: string;
  html: string;
  text?: string;
  verified: boolean;
  quality: string[];
  axe: string[];
  wave: string[];
  dom: string[];
  notes?: string;
};

type PageData = {
  url: string;
  title?: string;
  components: ComponentItem[];
  errors: ErrorItem[];
};

const COMPONENT_TYPES = [
  "button",
  "pseudo_button",
  "link",
  "navigation_link",
  "image",
  "background_image",
  "form",
  "form_control",
  "heading",
  "navigation",
  "table",
  "list",
  "search",
  "card",
  "breadcrumb",
  "pagination",
  "modal",
  "accordion",
  "tabs",
  "dropdown",
  "tooltip",
  "custom",
];

const QUALITY_OPTIONS = [
  "missing_h1",
  "broken_hierarchy",
  "too_many_h1",
  "empty",
  "generic",
  "no_href",
  "image_only",
  "no_name",
  "icon_only",
  "pseudo_button",
  "missing_alt",
  "empty_alt",
  "redundant_alt",
  "probably_content",
  "no_text_equivalent",
  "no_label",
  "placeholder_only",
  "no_name_attr",
  "no_submit",
  "complex_form",
  "no_headers",
  "no_caption",
  "many_links",
  "deep_nesting",
  "needs_manual_review",
];

const sampleData: PageData = {
  url: "https://example.gov/page-1",
  title: "Demo page",
  components: [
    {
      id: "cmp_001",
      autoType: "button",
      manualType: "button",
      manualSubtype: "icon_button",
      xpath: "/html/body/main/form/button[1]",
      html: '<button class="search-btn"><svg></svg></button>',
      text: "",
      verified: false,
      quality: ["icon_only", "no_name"],
      axe: ["button-name"],
      wave: ["low_contrast"],
      dom: ['<button class="search-btn"><svg></svg></button>'],
      notes: "Автоопределён как button",
    },
    {
      id: "cmp_002",
      autoType: "link",
      manualType: "navigation_link",
      manualSubtype: "header_link",
      xpath: "/html/body/header/nav/a[2]",
      html: '<a href="/services">Услуги</a>',
      text: "Услуги",
      verified: true,
      quality: [],
      axe: [],
      wave: [],
      dom: ['<a href="/services">Услуги</a>'],
      notes: "Проверено вручную",
    },
    {
      id: "cmp_003",
      autoType: "image",
      manualType: "image",
      manualSubtype: "content_image",
      xpath: "/html/body/main/article/img[1]",
      html: '<img src="/banner.jpg">',
      text: "",
      verified: false,
      quality: ["missing_alt"],
      axe: ["image-alt"],
      wave: ["missing_alt"],
      dom: ['<img src="/banner.jpg">'],
    },
  ],
  errors: [
    {
      id: "err_001",
      source: "axe",
      type: "button-name",
      targetXpath: "/html/body/main/form/button[1]",
      assignedComponentId: "cmp_001",
    },
    {
      id: "err_002",
      source: "wave",
      type: "low_contrast",
      targetXpath: "/html/body/main/form/button[1]/svg[1]",
      assignedComponentId: "cmp_001",
    },
    {
      id: "err_003",
      source: "axe",
      type: "image-alt",
      targetXpath: "/html/body/main/article/img[1]",
      assignedComponentId: "cmp_003",
    },
    {
      id: "err_004",
      source: "wave",
      type: "missing_first_level_heading",
      assignedComponentId: null,
      notes: "Пока не привязано к конкретному экземпляру",
    },
  ],
};

function sourceBadgeClass(source: ErrorSource) {
  if (source === "axe") return "bg-slate-900 text-white";
  if (source === "wave") return "bg-slate-200 text-slate-900";
  return "bg-amber-100 text-amber-900";
}

function componentIcon(type: string) {
  if (type.includes("button")) return <MousePointerClick className="h-4 w-4" />;
  if (type.includes("link")) return <Link2 className="h-4 w-4" />;
  if (type.includes("image")) return <ImageIcon className="h-4 w-4" />;
  if (type.includes("form") || type === "search") return <Search className="h-4 w-4" />;
  return <FileText className="h-4 w-4" />;
}

export default function A11yAnnotationMVP() {
  const [data, setData] = useState<PageData>(sampleData);
  const [selectedId, setSelectedId] = useState<string>(sampleData.components[0]?.id || "");
  const [urlInput, setUrlInput] = useState(sampleData.url);
  const [rawJson, setRawJson] = useState(JSON.stringify(sampleData, null, 2));
  const [filter, setFilter] = useState("all");
  const [newQuality, setNewQuality] = useState(QUALITY_OPTIONS[0]);
  const [newManualError, setNewManualError] = useState("");

  const selectedComponent = useMemo(
    () => data.components.find((c) => c.id === selectedId) || null,
    [data.components, selectedId]
  );

  const filteredComponents = useMemo(() => {
    if (filter === "all") return data.components;
    if (filter === "verified") return data.components.filter((c) => c.verified);
    if (filter === "unverified") return data.components.filter((c) => !c.verified);
    return data.components.filter((c) => c.manualType === filter || c.autoType === filter);
  }, [data.components, filter]);

  const unassignedErrors = data.errors.filter((e) => !e.assignedComponentId);
  const selectedErrors = data.errors.filter((e) => e.assignedComponentId === selectedId);

  function updateSelected(patch: Partial<ComponentItem>) {
    if (!selectedComponent) return;
    setData((prev) => ({
      ...prev,
      components: prev.components.map((c) => (c.id === selectedComponent.id ? { ...c, ...patch } : c)),
    }));
  }

  function toggleQualityFlag(flag: string) {
    if (!selectedComponent) return;
    const hasFlag = selectedComponent.quality.includes(flag);
    updateSelected({
      quality: hasFlag
        ? selectedComponent.quality.filter((q) => q !== flag)
        : [...selectedComponent.quality, flag],
    });
  }

  function addQualityFlag() {
    if (!selectedComponent || !newQuality) return;
    if (selectedComponent.quality.includes(newQuality)) return;
    updateSelected({ quality: [...selectedComponent.quality, newQuality] });
  }

  function attachError(errorId: string, componentId: string) {
    setData((prev) => ({
      ...prev,
      errors: prev.errors.map((e) => (e.id === errorId ? { ...e, assignedComponentId: componentId } : e)),
      components: prev.components.map((c) => {
        if (c.id !== componentId) return c;
        const err = prev.errors.find((e) => e.id === errorId);
        if (!err) return c;
        if (err.source === "axe") {
          return c.axe.includes(err.type) ? c : { ...c, axe: [...c.axe, err.type] };
        }
        if (err.source === "wave") {
          return c.wave.includes(err.type) ? c : { ...c, wave: [...c.wave, err.type] };
        }
        return c;
      }),
    }));
  }

  function detachError(errorId: string) {
    setData((prev) => {
      const err = prev.errors.find((e) => e.id === errorId);
      return {
        ...prev,
        errors: prev.errors.map((e) => (e.id === errorId ? { ...e, assignedComponentId: null } : e)),
        components: prev.components.map((c) => {
          if (!err || c.id !== err.assignedComponentId) return c;
          return {
            ...c,
            axe: err.source === "axe" ? c.axe.filter((x) => x !== err.type) : c.axe,
            wave: err.source === "wave" ? c.wave.filter((x) => x !== err.type) : c.wave,
          };
        }),
      };
    });
  }

  function addManualError() {
    if (!selectedComponent || !newManualError.trim()) return;
    const id = `err_${Date.now()}`;
    setData((prev) => ({
      ...prev,
      errors: [
        ...prev.errors,
        {
          id,
          source: "manual",
          type: newManualError.trim(),
          assignedComponentId: selectedComponent.id,
        },
      ],
    }));
    setNewManualError("");
  }

  function addComponent() {
    const id = `cmp_${Date.now()}`;
    const newComponent: ComponentItem = {
      id,
      autoType: "custom",
      manualType: "custom",
      manualSubtype: "",
      xpath: "",
      html: "",
      text: "",
      verified: false,
      quality: [],
      axe: [],
      wave: [],
      dom: [],
      notes: "Добавлен вручную",
    };
    setData((prev) => ({ ...prev, components: [...prev.components, newComponent] }));
    setSelectedId(id);
  }

  function deleteComponent() {
    if (!selectedComponent) return;
    const next = data.components.filter((c) => c.id !== selectedComponent.id);
    setData((prev) => ({
      ...prev,
      components: prev.components.filter((c) => c.id !== selectedComponent.id),
      errors: prev.errors.map((e) =>
        e.assignedComponentId === selectedComponent.id ? { ...e, assignedComponentId: null } : e
      ),
    }));
    setSelectedId(next[0]?.id || "");
  }

  function loadJson() {
    try {
      const parsed = JSON.parse(rawJson) as PageData;
      setData(parsed);
      setUrlInput(parsed.url || "");
      setSelectedId(parsed.components[0]?.id || "");
    } catch (e) {
      alert("JSON не удалось прочитать");
    }
  }

  function exportJson() {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "annotated_page.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="min-h-screen bg-slate-50 p-4 md:p-6">
      <div className="mx-auto grid max-w-7xl grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)_360px]">
        <Card className="rounded-2xl shadow-sm">
          <CardHeader>
            <CardTitle className="text-lg">Страница и данные</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>URL</Label>
              <Input value={urlInput} onChange={(e) => setUrlInput(e.target.value)} placeholder="https://site.gov/page" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button className="rounded-xl">Открыть страницу</Button>
              <Button variant="secondary" className="rounded-xl" onClick={exportJson}>
                <Save className="mr-2 h-4 w-4" />
                Сохранить JSON
              </Button>
            </div>

            <Separator />

            <div className="space-y-2">
              <Label>Импорт / правка JSON</Label>
              <Textarea
                value={rawJson}
                onChange={(e) => setRawJson(e.target.value)}
                className="min-h-[220px] font-mono text-xs"
              />
              <div className="flex gap-2">
                <Button variant="outline" className="rounded-xl" onClick={loadJson}>
                  <Upload className="mr-2 h-4 w-4" />
                  Загрузить JSON
                </Button>
                <Button variant="outline" className="rounded-xl" onClick={() => setRawJson(JSON.stringify(data, null, 2))}>
                  Обновить из состояния
                </Button>
              </div>
            </div>

            <Separator />

            <div className="grid grid-cols-2 gap-2 text-sm">
              <div className="rounded-2xl bg-slate-100 p-3">
                <div className="text-slate-500">Компоненты</div>
                <div className="text-2xl font-semibold">{data.components.length}</div>
              </div>
              <div className="rounded-2xl bg-slate-100 p-3">
                <div className="text-slate-500">Ошибки</div>
                <div className="text-2xl font-semibold">{data.errors.length}</div>
              </div>
            </div>

            <div className="rounded-2xl border border-dashed border-slate-300 p-3 text-sm text-slate-600">
              Этот MVP предполагает, что Playwright и backend позже будут подставлять сюда DOM, список компонентов и AXE/WAVE ошибки.
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-2xl shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-lg">Компоненты страницы</CardTitle>
            <div className="flex gap-2">
              <Select value={filter} onValueChange={setFilter}>
                <SelectTrigger className="w-[160px] rounded-xl bg-white">
                  <SelectValue placeholder="Фильтр" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Все</SelectItem>
                  <SelectItem value="verified">Только verified</SelectItem>
                  <SelectItem value="unverified">Только unverified</SelectItem>
                  {COMPONENT_TYPES.map((type) => (
                    <SelectItem key={type} value={type}>
                      {type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button variant="outline" className="rounded-xl" onClick={addComponent}>
                <Plus className="mr-2 h-4 w-4" />
                Добавить
              </Button>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
            <ScrollArea className="h-[72vh] rounded-2xl border bg-white">
              <div className="space-y-2 p-3">
                {filteredComponents.map((component) => (
                  <button
                    key={component.id}
                    onClick={() => setSelectedId(component.id)}
                    className={`w-full rounded-2xl border p-3 text-left transition ${
                      selectedId === component.id
                        ? "border-slate-900 bg-slate-900 text-white"
                        : "border-slate-200 bg-white hover:border-slate-400"
                    }`}
                  >
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 font-medium">
                        {componentIcon(component.manualType || component.autoType)}
                        <span>{component.manualType || component.autoType}</span>
                      </div>
                      {component.verified ? (
                        <CheckCircle2 className="h-4 w-4" />
                      ) : (
                        <AlertCircle className="h-4 w-4 opacity-70" />
                      )}
                    </div>
                    <div className="line-clamp-2 text-xs opacity-80">{component.xpath || "xpath не задан"}</div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <Badge variant="secondary">auto: {component.autoType}</Badge>
                      {component.manualSubtype ? <Badge variant="secondary">{component.manualSubtype}</Badge> : null}
                    </div>
                  </button>
                ))}
              </div>
            </ScrollArea>

            {selectedComponent ? (
              <div className="space-y-4">
                <Tabs defaultValue="component">
                  <TabsList className="grid w-full grid-cols-3 rounded-2xl">
                    <TabsTrigger value="component">Компонент</TabsTrigger>
                    <TabsTrigger value="errors">Ошибки</TabsTrigger>
                    <TabsTrigger value="quality">Quality</TabsTrigger>
                  </TabsList>

                  <TabsContent value="component" className="mt-4 space-y-4">
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="space-y-2">
                        <Label>Авто тип</Label>
                        <Input value={selectedComponent.autoType} disabled />
                      </div>
                      <div className="space-y-2">
                        <Label>Ручной тип</Label>
                        <Select
                          value={selectedComponent.manualType}
                          onValueChange={(value) => updateSelected({ manualType: value })}
                        >
                          <SelectTrigger className="rounded-xl bg-white">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {COMPONENT_TYPES.map((type) => (
                              <SelectItem key={type} value={type}>
                                {type}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    </div>

                    <div className="space-y-2">
                      <Label>Ручной подтип</Label>
                      <Input
                        value={selectedComponent.manualSubtype}
                        onChange={(e) => updateSelected({ manualSubtype: e.target.value })}
                        placeholder="например: icon_button"
                      />
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="space-y-2">
                        <Label>XPath</Label>
                        <Input
                          value={selectedComponent.xpath}
                          onChange={(e) => updateSelected({ xpath: e.target.value })}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label>Text</Label>
                        <Input
                          value={selectedComponent.text || ""}
                          onChange={(e) => updateSelected({ text: e.target.value })}
                        />
                      </div>
                    </div>

                    <div className="space-y-2">
                      <Label>HTML / DOM фрагмент</Label>
                      <Textarea
                        className="min-h-[120px] font-mono text-xs"
                        value={selectedComponent.html}
                        onChange={(e) => updateSelected({ html: e.target.value, dom: [e.target.value] })}
                      />
                    </div>

                    <div className="space-y-2">
                      <Label>Заметки</Label>
                      <Textarea
                        value={selectedComponent.notes || ""}
                        onChange={(e) => updateSelected({ notes: e.target.value })}
                        placeholder="Что важно по этому компоненту"
                      />
                    </div>

                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant={selectedComponent.verified ? "secondary" : "default"}
                        className="rounded-xl"
                        onClick={() => updateSelected({ verified: !selectedComponent.verified })}
                      >
                        {selectedComponent.verified ? "Снять verified" : "Пометить verified"}
                      </Button>
                      <Button variant="destructive" className="rounded-xl" onClick={deleteComponent}>
                        <Trash2 className="mr-2 h-4 w-4" />
                        Удалить компонент
                      </Button>
                    </div>
                  </TabsContent>

                  <TabsContent value="errors" className="mt-4 space-y-4">
                    <div className="grid gap-4 md:grid-cols-2">
                      <Card className="rounded-2xl">
                        <CardHeader>
                          <CardTitle className="text-base">Ошибки выбранного компонента</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2">
                          {selectedErrors.length === 0 ? (
                            <div className="text-sm text-slate-500">Ошибки не привязаны</div>
                          ) : (
                            selectedErrors.map((error) => (
                              <div key={error.id} className="rounded-2xl border p-3">
                                <div className="mb-2 flex items-center justify-between gap-2">
                                  <Badge className={sourceBadgeClass(error.source)}>{error.source}</Badge>
                                  <Button variant="ghost" size="sm" onClick={() => detachError(error.id)}>
                                    Открепить
                                  </Button>
                                </div>
                                <div className="font-medium">{error.type}</div>
                                <div className="mt-1 text-xs text-slate-500">{error.targetXpath || "без xpath"}</div>
                              </div>
                            ))
                          )}

                          <Separator className="my-3" />
                          <div className="space-y-2">
                            <Label>Добавить manual error</Label>
                            <div className="flex gap-2">
                              <Input
                                value={newManualError}
                                onChange={(e) => setNewManualError(e.target.value)}
                                placeholder="например: low_contrast_manual"
                              />
                              <Button onClick={addManualError}>Добавить</Button>
                            </div>
                          </div>
                        </CardContent>
                      </Card>

                      <Card className="rounded-2xl">
                        <CardHeader>
                          <CardTitle className="text-base">Непривязанные ошибки</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2">
                          {unassignedErrors.length === 0 ? (
                            <div className="text-sm text-slate-500">Все ошибки уже привязаны</div>
                          ) : (
                            unassignedErrors.map((error) => (
                              <div key={error.id} className="rounded-2xl border p-3">
                                <div className="mb-2 flex items-center justify-between gap-2">
                                  <Badge className={sourceBadgeClass(error.source)}>{error.source}</Badge>
                                  <Button size="sm" onClick={() => attachError(error.id, selectedComponent.id)}>
                                    Привязать к выбранному
                                  </Button>
                                </div>
                                <div className="font-medium">{error.type}</div>
                                <div className="mt-1 text-xs text-slate-500">{error.targetXpath || "без xpath"}</div>
                                {error.notes ? <div className="mt-1 text-xs text-slate-500">{error.notes}</div> : null}
                              </div>
                            ))
                          )}
                        </CardContent>
                      </Card>
                    </div>
                  </TabsContent>

                  <TabsContent value="quality" className="mt-4 space-y-4">
                    <Card className="rounded-2xl">
                      <CardHeader>
                        <CardTitle className="text-base">Quality признаки</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-2">
                          {QUALITY_OPTIONS.map((flag) => {
                            const checked = selectedComponent.quality.includes(flag);
                            return (
                              <label key={flag} className="flex items-center gap-3 rounded-2xl border p-3 text-sm">
                                <Checkbox checked={checked} onCheckedChange={() => toggleQualityFlag(flag)} />
                                <span>{flag}</span>
                              </label>
                            );
                          })}
                        </div>
                        <div className="flex gap-2">
                          <Select value={newQuality} onValueChange={setNewQuality}>
                            <SelectTrigger className="w-[260px] rounded-xl bg-white">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {QUALITY_OPTIONS.map((flag) => (
                                <SelectItem key={flag} value={flag}>
                                  {flag}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <Button variant="outline" onClick={addQualityFlag}>Добавить флаг</Button>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {selectedComponent.quality.map((flag) => (
                            <Badge key={flag} variant="secondary">{flag}</Badge>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  </TabsContent>
                </Tabs>
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed p-8 text-center text-slate-500">
                Выбери компонент слева
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="rounded-2xl shadow-sm">
          <CardHeader>
            <CardTitle className="text-lg">Сводка</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-2xl bg-slate-100 p-3">
              <div className="text-sm text-slate-500">URL</div>
              <div className="break-all font-medium">{data.url}</div>
            </div>

            <div className="space-y-2">
              <div className="text-sm font-medium">По типам компонентов</div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(
                  data.components.reduce<Record<string, number>>((acc, component) => {
                    const key = component.manualType || component.autoType;
                    acc[key] = (acc[key] || 0) + 1;
                    return acc;
                  }, {})
                ).map(([key, value]) => (
                  <Badge key={key} variant="secondary">{key}: {value}</Badge>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-sm font-medium">Непривязанные ошибки</div>
              <div className="rounded-2xl border p-3 text-2xl font-semibold">{unassignedErrors.length}</div>
            </div>

            <div className="space-y-2">
              <div className="text-sm font-medium">Что дальше подключить</div>
              <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">
                <li>загрузку URL через backend и Playwright</li>
                <li>автосбор DOM и начального списка компонентов</li>
                <li>AXE запуск и импорт результатов</li>
                <li>WAVE импорт и ручную привязку</li>
                <li>экспорт в итоговый исследовательский JSON</li>
              </ul>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
