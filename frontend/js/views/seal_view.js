export const sealViewTemplate = `
<section class="panel">
  <h2>封批升代</h2>
  <p class="hint">校准有效且无开放游标时方可封批；封批快照代即当前封批代，与温谱导出代同态。</p>
  <div class="form-row">
    <div><label>批次</label>
      <select v-model.number="sealForm.batch_id"><option v-for="b in sealableBatches" :key="b.id" :value="b.id">{{ b.batch_code }} G{{ b.generation }}</option></select>
    </div>
    <div><label>快照附言</label><input v-model="sealForm.manifest"/></div>
  </div>
  <div class="actions"><button @click="doSeal">执行封批</button></div>
  <table class="data-table">
    <thead><tr><th>批次</th><th>封批快照代</th><th>当前封批代</th><th>代际一致</th><th>封批时间</th><th>附言摘要</th></tr></thead>
    <tbody>
      <tr v-for="s in seals" :key="s.id">
        <td>{{ s.batch_code }}</td>
        <td>G{{ s.batch_generation }}</td>
        <td>G{{ currentGenerationOf(s.batch_id) }}</td>
        <td><span :class="sealGenerationAligned(s) ? 'ok' : 'bad'">{{ sealGenerationAligned(s) ? "一致" : "错代" }}</span></td>
        <td>{{ s.sealed_at }}</td>
        <td>{{ shortText(s.manifest) }}</td>
      </tr>
    </tbody>
  </table>
</section>
`;
